/* Bind a charging service process before validating or signalling it.
 * No PID-based kill fallback: an unsupported pidfd ABI fails closed. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

static int decimal(const char *s, unsigned long long *n) {
    if (!*s) return -1;
    for (const char *p=s; *p; p++) if (*p<'0'||*p>'9') return -1;
    errno=0; char *end; *n=strtoull(s,&end,10);
    return errno||*end ? -1 : 0;
}
static int read_proc(pid_t pid, const char *name, char *buf, size_t cap) {
    char path[64];
    if (snprintf(path,sizeof(path),"/proc/%ld/%s",(long)pid,name)>=(int)sizeof(path)) return -1;
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if (fd<0) return -1;
    size_t used=0;
    while (used<cap) {
        ssize_t n=read(fd,buf+used,cap-used);
        if (n<0 && errno==EINTR) continue;
        if (n<0) { close(fd); return -1; }
        if (!n) { close(fd); return (int)used; }
        used+=(size_t)n;
    }
    close(fd); return -1;
}
static int start_time(pid_t pid, unsigned long long *ticks) {
    char text[4096]; int n=read_proc(pid,"stat",text,sizeof(text)-1);
    if (n<0) return -1;
    text[n]=0;
    char *end=strrchr(text,')');
    if (!end||end[1]!=' ') return -1;
    char *cursor=end+2,*save=NULL,*part=strtok_r(cursor," \n",&save);
    /* proc stat field3 is the first token after the parenthesized comm. */
    for (int field=3; field<22 && part; field++) part=strtok_r(NULL," \n",&save);
    return part ? decimal(part,ticks) : -1;
}
static int exact_args(pid_t pid, int argc, char **argv) {
    char text[8192]; int n=read_proc(pid,"cmdline",text,sizeof(text));
    if (n<=0) return -1;
    size_t offset=0;
    for (int i=0; i<argc; i++) {
        size_t length=strlen(argv[i])+1;
        if (length>(size_t)n-offset || memcmp(text+offset,argv[i],length)) return -1;
        offset+=length;
    }
    return offset==(size_t)n ? 0 : -1;
}
static int bind_checked(pid_t pid, unsigned long long expected, int argc, char **argv, unsigned long long *observed) {
    int fd=(int)syscall(SYS_pidfd_open,pid,0);
    if (fd<0) return -1;
    unsigned long long first,second;
    if (start_time(pid,&first)||(expected && first!=expected)||exact_args(pid,argc,argv)||
        start_time(pid,&second)||second!=first||syscall(SYS_pidfd_send_signal,fd,0,NULL,0)) {
        close(fd); return -1;
    }
    if (observed) *observed=first;
    return fd;
}
int main(int argc, char **argv) {
    unsigned long long number,ticks=0,observed;
    int stamp=argc>1 && !strcmp(argv[1],"stamp");
    int separator=stamp?3:4;
    if (argc<separator+2 || (!stamp && strcmp(argv[1],"check")&&strcmp(argv[1],"term")) ||
        decimal(argv[2],&number)||number<=1||number>INT_MAX||
        (!stamp && (decimal(argv[3],&ticks)||!ticks))||strcmp(argv[separator],"--")) return 2;
    int fd=bind_checked((pid_t)number,ticks,argc-separator-1,argv+separator+1,&observed);
    if (fd<0) return 1;
    int result=0;
    if (!strcmp(argv[1],"term") && syscall(SYS_pidfd_send_signal,fd,SIGTERM,NULL,0)) result=1;
    if (stamp && printf("%llu\n",observed)<0) result=1;
    close(fd); return result;
}
