/* Mainline-only watchdog owner: real worker progress, authenticated by kernel
 * credentials. Pause/network waits pulse from the audio thread itself.
 *
 * Memory pressure must not stop the owner (q2-factory projects/kernel/STATUS.md, KRN-001):
 * with file pages thrashing, an unlocked owner did not reach its keepalive
 * for 5 s while the kernel and the rest of user space ran, and the reset
 * left no record. The owner locks its pages, runs in a real-time class and
 * is exempt from the OOM killer, so only its own policy ends supervision:
 * it keeps the watchdog fed while the player's pulses arrive and stops when
 * they do not (UI 8 s, audio 25 s, as before). Every ending and every late
 * keepalive is written to the kernel log and the pmsg record, which survive
 * the reset. */
#define _GNU_SOURCE
#include <linux/watchdog.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include "watchdog-transfer.h"
static volatile sig_atomic_t stopping;
static void stop(int sig) { (void)sig; stopping = 1; }
static long long now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec*1000LL+t.tv_nsec/1000000; }
static int number(const char *path) { int n=0; FILE *f=fopen(path,"r"); if(f) { if(fscanf(f,"%d",&n)!=1) n=0; fclose(f); } return n; }
static void record(const char *path,int n) { FILE *f=fopen(path,"w"); if(!f) exit(2); fprintf(f,"%d\n",n); if(fclose(f)) exit(2); }
/* A USB handoff restarts supervision within the same kernel boot. Its pending
 * increment is already charged: reaching the limit must not reject continuation
 * of that very attempt. A new uncounted boot still obeys the three-failure gate. */
/* The trusted launcher verifies a bounded app image before it has a child.
 * This phase has its own deadline; the worker-health deadline stays 30s. */
static int preparation_owned(int player, int launcher, int marker, int launcher_live) {
    return player==0 && launcher>1 && marker==launcher && launcher_live;
}
static int phase_expired(long long elapsed, int preparing) {
    return elapsed > (preparing ? 120000LL : 30000LL);
}
static int failed_boot_limit(int failures, int counted) { return failures>=3 && !counted; }
/* Credit only an acknowledged disarm with authenticated, confirmed live workers.
 * Preserve earlier crash history; unconfirmed stops and watchdog exits get none. */
static int clean_disarm_failures(int failures, int confirmed, int live) {
    if(!confirmed || !live) return failures;
    return failures>0 ? failures-1 : 0;
}
static int persist_failures(int n) {
    const char *temp="/state/app/boot-failures.new";
    FILE *f=fopen(temp,"w");
    if(!f) return -1;
    int bad=fprintf(f,"%d\n",n)<0 || fflush(f) || fsync(fileno(f));
    if(fclose(f)) bad=1;
    if(bad || rename(temp,"/state/app/boot-failures")) return -1;
    int dir=open("/state/app",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(dir<0) return -1;
    bad=fsync(dir); close(dir); return bad;
}
static int claim_disarm_credit(void) {
    /* Claim before changing persistent state. A failed directory fsync can
     * leave the rename visible; a later same-boot supervisor must never apply
     * a second credit to that same pending attempt. RAM claims survive service
     * restarts and disappear only at a new kernel boot. */
    int claim=open("/run/q2nd/boot-credit-claimed",O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if(claim<0) return 0;
    int good=write(claim,"1\n",2)==2;
    if(close(claim)) good=0;
    return good;
}
static void credit_clean_disarm(int failures, int confirmed, int live) {
    if(!confirmed || !live || access("/run/q2nd/boot-counted",F_OK) || !claim_disarm_credit()) return;
    int remaining=clean_disarm_failures(failures,confirmed,live);
    if(!persist_failures(remaining) && !unlink("/run/q2nd/boot-counted")) {
        unlink("/run/q2nd/boot-credit-claimed");
    }
}
/* Records that outlive a watchdog reset: the ramoops console (KERN_CRIT
 * passes the console level of sysctl.conf) and the pmsg record. Both are
 * opened once; a write is one system call on locked memory. */
static int journal_kmsg=-1, journal_pmsg=-1;
static void journal(const char *format, ...) {
    char line[200]="<2>q2-supervisor: "; int head=strlen(line);
    va_list arguments; va_start(arguments,format);
    int n=vsnprintf(line+head,sizeof(line)-head-1,format,arguments);
    va_end(arguments);
    if(n<0) return;
    if(n>(int)sizeof(line)-head-2) n=sizeof(line)-head-2;
    line[head+n]='\n';
    if(journal_kmsg>=0 && write(journal_kmsg,line,head+n+1)<0) {}
    if(journal_pmsg>=0 && write(journal_pmsg,line+3,head+n-2)<0) {}
}
/* A late keepalive is the early sign of the stall that ends in a reset. */
static int keepalive_late(long long previous, long long current) {
    return previous>0 && current-previous>1000;
}
static void harden(void) {
    journal_kmsg=open("/dev/kmsg",O_WRONLY|O_CLOEXEC);
    journal_pmsg=open("/dev/pmsg0",O_WRONLY|O_CLOEXEC);
    /* Touch the stack the loop will use before locking it. */
    volatile char stack[32768]; for(unsigned i=0;i<sizeof(stack);i+=1024) stack[i]=0;
    int locked=mlockall(MCL_CURRENT|MCL_FUTURE)==0;
    struct sched_param priority={.sched_priority=10};
    int realtime=sched_setscheduler(0,SCHED_FIFO,&priority)==0;
    int exempt=0, adj=open("/proc/self/oom_score_adj",O_WRONLY|O_CLOEXEC);
    if(adj>=0) { exempt=write(adj,"-1000\n",6)==6; close(adj); }
    printf("Q2_SUPERVISOR_HARDENED locked=%d realtime=%d oom_exempt=%d\n",locked,realtime,exempt);
}
static int rescue(int fd) {
    record("/run/q2nd/rescue",1);
    unlink("/run/q2nd/supervisor.pid");
    puts("Q2_SUPERVISOR_RESCUE");
    int option=WDIOS_DISABLECARD;
    return ioctl(fd,WDIOC_SETOPTIONS,&option) ? 2 : 0;
}
int main(void) {
    umask(077);
    int fd=q2_watchdog_claim("/run/q2-wdt/owner.sock");
    if(fd<0 && errno==ENOENT) {
        /* Explicit service restart after an orderly disarm. */
        fd=open("/dev/watchdog0",O_RDWR|O_CLOEXEC);
        int timeout=5;
        if(fd<0 || ioctl(fd,WDIOC_SETTIMEOUT,&timeout) || timeout!=5) return 2;
    }
    if(fd<0) { perror("claim watchdog"); return 2; }
    signal(SIGTERM,stop); signal(SIGINT,stop);
    record("/run/q2nd/supervisor.pid",getpid());
    int failures=number("/state/app/boot-failures");
    char status[32]={0}; FILE *state=fopen("/run/q2/state-status","r");
    if(state) { if(!fgets(status,sizeof(status),state)) status[0]=0; fclose(state); }
    int counted=access("/run/q2nd/boot-counted",F_OK)==0;
    if(failed_boot_limit(failures,counted) || strcmp(status,"ready\n")) {
        return rescue(fd);
    }
    if(!counted) {
        if(persist_failures(failures+1)) return rescue(fd);
        failures++;
        record("/run/q2nd/boot-counted",1);
    }
    int sock=socket(AF_UNIX,SOCK_DGRAM|SOCK_CLOEXEC|SOCK_NONBLOCK,0), enabled=1;
    struct sockaddr_un addr={.sun_family=AF_UNIX}; strcpy(addr.sun_path,"/run/q2nd/health.sock");
    if(sock<0 || setsockopt(sock,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled)) || bind(sock,(void*)&addr,sizeof(addr))) { perror("health socket"); return 2; }
    harden();
    puts("Q2_SUPERVISOR_STARTED"); fflush(stdout);
    long long start=now(), ui=0,audio=0,healthy=0,fed=0; int player=0,confirmed=0,restarts=0,was_preparing=0;
    while(!stopping) {
        long long tick=now(); int current=number("/run/q2nd/player.pid");
        if(current!=player) {
            /* Cover the launcher's bounded gap as well as the new process.
             * A missing PID must not turn a recoverable crash into a reset. */
            if(restarts<3) start=tick;
            if(current>0) restarts++;
            player=current; ui=audio=healthy=0; confirmed=0;
            unlink("/run/q2nd/healthy");
        }
        for(int i=0;i<64;i++) {
            char byte=0, control[CMSG_SPACE(sizeof(struct ucred))]={0};
            struct iovec iov={.iov_base=&byte,.iov_len=1};
            struct msghdr msg={.msg_iov=&iov,.msg_iovlen=1,.msg_control=control,.msg_controllen=sizeof(control)};
            if(recvmsg(sock,&msg,MSG_DONTWAIT)!=1) break;
            struct cmsghdr *c=CMSG_FIRSTHDR(&msg);
            if(msg.msg_flags&(MSG_TRUNC|MSG_CTRUNC) || !c || c->cmsg_level!=SOL_SOCKET || c->cmsg_type!=SCM_CREDENTIALS || c->cmsg_len!=CMSG_LEN(sizeof(struct ucred))) continue;
            struct ucred cred; memcpy(&cred,CMSG_DATA(c),sizeof(cred));
            if(cred.uid || cred.pid!=player || !player) continue;
            if(byte=='U') ui=tick;
            if(byte=='A') audio=tick;
        }
        int live=ui && audio && tick-ui<8000 && tick-audio<25000;
        if(live) {
            if(!healthy) healthy=tick;
            if(!confirmed && tick-healthy>=5000) { record("/run/q2nd/healthy",player); confirmed=1; puts("Q2_PLAYER_HEALTHY"); fflush(stdout); }
            if(failures>=0 && tick-healthy>=60000 && tick-ui<2000 && tick-audio<2000) {
                if(!persist_failures(0)) {
                    failures=-1; restarts=0;
                    /* A fully qualified minute closes any earlier uncertain
                     * disarm transaction; successful qualification is the
                     * pre-existing authority to clear the failure history. */
                    if(!unlink("/run/q2nd/boot-counted")) unlink("/run/q2nd/boot-credit-claimed");
                }
            }
        } else { healthy=0; unlink("/run/q2nd/healthy"); confirmed=0; }
        int preparing=0;
        if(!player) {
            int launcher=number("/run/q2nd/launcher.pid");
            int prep=number("/run/q2nd/app-preparing");
            preparing=preparation_owned(player,launcher,prep,launcher>1 && !kill(launcher,0));
        }
        if(was_preparing && !preparing) start=tick;
        was_preparing=preparing;
        if(phase_expired(tick-start,preparing) && !live) {
            journal("%s: player %d, last UI pulse %lld ms ago, audio %lld ms; the watchdog resets in 5 s",
                preparing ? "app preparation expired" : "player health expired",player,
                ui ? tick-ui : -1,audio ? tick-audio : -1);
            puts(preparing ? "Q2_APP_PREPARATION_EXPIRED" : "Q2_PLAYER_HEALTH_EXPIRED");
            fflush(stdout); close(fd); return 3;
        }
        long long feeding=now();
        if(keepalive_late(fed,feeding)) journal("keepalive %lld ms after the previous one",feeding-fed);
        if(ioctl(fd,WDIOC_KEEPALIVE,NULL)) { journal("keepalive failed"); return 2; }
        fed=feeding;
        struct pollfd p={.fd=sock,.events=POLLIN}; poll(&p,1,200);
    }
    int option=WDIOS_DISABLECARD;
    if(ioctl(fd,WDIOC_SETOPTIONS,&option)) return 2;
    long long tick=now();
    int live=ui && audio && tick-ui<8000 && tick-audio<25000;
    credit_clean_disarm(failures,confirmed,live);
    close(fd); close(sock); unlink(addr.sun_path); unlink("/run/q2nd/supervisor.pid"); unlink("/run/q2nd/healthy");
    return 0;
}
