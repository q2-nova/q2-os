"""Actual production C policy, generated state sequences, no device/config input."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import json

ROOT = Path(__file__).resolve().parents[1]


class SupervisorBootAccounting(unittest.TestCase):
    def test_real_preparation_ownership_and_separate_deadlines(self):
        base=ROOT/'build/perf';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root=Path(directory);source=root/'policy.c';exe=root/'policy'
            source.write_text('''#define main supervisor_main
#include "player-supervisor.c"
#undef main
int main(void) {
    if (!preparation_owned(0,42,42,1)) return 1;
    if (preparation_owned(9,42,42,1) || preparation_owned(0,42,43,1) ||
        preparation_owned(0,42,42,0) || preparation_owned(0,1,1,1)) return 2;
    if (phase_expired(30001,1) || phase_expired(120000,1) || !phase_expired(120001,1)) return 3;
    if (phase_expired(30000,0) || !phase_expired(30001,0)) return 4;
    /* The first keepalive is never late; later ones are after more than 1 s. */
    if (keepalive_late(0,9000) || keepalive_late(1000,2000) || !keepalive_late(1000,2001)) return 5;
    return 0;
}
''')
            subprocess.run(['cc','-Wall','-Wextra','-Werror','-O2','-I',str(ROOT/'os/probes'),str(source),'-o',str(exe)],check=True,capture_output=True)
            self.assertEqual(subprocess.run([str(exe)],timeout=3).returncode,0)

    def test_real_persistence_faults_cannot_credit_an_attempt_twice(self):
        base = ROOT / 'build/perf'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            (root / 'state').mkdir()
            (root / 'run').mkdir()
            source = (ROOT / 'os/probes/player-supervisor.c').read_text()
            # Substitute only the owned filesystem namespace. Production I/O,
            # rename ordering and reconciliation all execute unchanged.
            source = source.replace('/state/app', 'state').replace('/run/q2nd', 'run')
            counter = json.dumps('state/boot-failures')
            pending = json.dumps('run/boot-counted')
            claim = json.dumps('run/boot-credit-claimed')
            wrapper = root / 'fault.c'
            wrapper.write_text('''#define fsync injected_fsync
#define main production_supervisor_main
#include "supervisor.c"
#undef main
#undef fsync
extern int fsync(int);
static int fault_mode;
int injected_fsync(int fd) {
    struct stat st; if(fstat(fd,&st)) return -1;
    if((fault_mode==1 && S_ISREG(st.st_mode)) || (fault_mode==2 && S_ISDIR(st.st_mode))) {
        fault_mode=0; errno=EIO; return -1;
    }
    return fsync(fd);
}
int main(int argc,char **argv) {
    if(argc!=2) return 2;
    int mode=atoi(argv[1]);
    const char *counter=COUNTER, *pending=PENDING, *claim=CLAIM;
    unlink(claim); record(counter,3); record(pending,1);
    fault_mode=mode;
    credit_clean_disarm(3,1,1);
    if(fault_mode) return 3; /* The selected real fsync stage was reached. */
    int first=number(counter);
    if(first!=(mode==1?3:2)) return 4;
    if(mode && (access(pending,F_OK) || access(claim,F_OK))) return 5;
    if(!mode && (!access(pending,F_OK) || !access(claim,F_OK))) return 6;
    /* Recreate a supervisor's local count from the visible persistent file. */
    credit_clean_disarm(number(counter),1,1);
    if(number(counter)!=first || number(counter)<2) return 7;
    return 0;
}
'''.replace('COUNTER', counter).replace('PENDING', pending).replace('CLAIM', claim))
            for variant, content in (
                ('candidate', source),
                ('nonexclusive-claim-control', source.replace('O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC', 'O_WRONLY|O_CREAT|O_CLOEXEC')),
            ):
                (root / 'supervisor.c').write_text(content)
                compiled = subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(ROOT / 'os/probes'), str(wrapper), '-o', str(root / 'fault')], capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                for mode in (0, 1, 2):
                    result = subprocess.run([str(root / 'fault'), str(mode)], timeout=3, cwd=root)
                    if variant == 'candidate':
                        self.assertEqual(result.returncode, 0, (variant, mode))
                    elif mode == 2:
                        self.assertNotEqual(result.returncode, 0, 'post-rename fault must expose repeated-credit control')

    def test_clean_restarts_and_unclean_boots_preserve_failure_history(self):
        base = ROOT / 'build/perf'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            wrapper = root / 'policy.c'
            wrapper.write_text('''#define main production_supervisor_main
#include "supervisor.c"
#undef main
#include <assert.h>
#undef assert
#define assert(condition) do { if(!(condition)) return 1; } while(0)
int main(void) {
    /* Three rapid deliberate healthy reboots never become three failed boots. */
    int failures=0;
    for(int boot=0;boot<20;boot++) {
        assert(!failed_boot_limit(failures,0)); failures++;
        failures=clean_disarm_failures(failures,1,1);
        assert(failures==0);
    }
    /* USB's unconfirmed stop neither earns credit nor rejects same-boot return. */
    failures=2; assert(!failed_boot_limit(failures,0)); failures++;
    assert(clean_disarm_failures(failures,0,1)==3);
    assert(!failed_boot_limit(failures,1));
    assert(failed_boot_limit(failures,0));
    /* Healthy intentional stop removes this attempt, preserving two old faults. */
    assert(clean_disarm_failures(failures,1,1)==2);
    assert(!failed_boot_limit(2,0));
    /* Genuine watchdog/unclean resets never take the orderly-disarm path. */
    failures=0;
    for(int boot=0;boot<3;boot++) { assert(!failed_boot_limit(failures,0)); failures++; }
    assert(failed_boot_limit(failures,0));
    for(int count=0;count<=8;count++) {
        assert(clean_disarm_failures(count,0,0)==count);
        assert(clean_disarm_failures(count,0,1)==count);
        assert(clean_disarm_failures(count,1,0)==count);
        assert(clean_disarm_failures(count,1,1)==(count>0?count-1:0));
        assert(!failed_boot_limit(count,1));
        assert(failed_boot_limit(count,0)==(count>=3));
    }
    /* A previously qualified minute uses the internal -1 sentinel. */
    assert(clean_disarm_failures(-1,1,1)==0);
    return 0;
}
''')
            source = (ROOT / 'os/probes/player-supervisor.c').read_text()
            for variant, content in (
                ('candidate', source),
                ('old-sameboot-limit', source.replace('return failures>=3 && !counted;', '(void)counted; return failures>=3;')),
                ('missing-orderly-credit', source.replace('return failures>0 ? failures-1 : 0;', 'return failures;')),
            ):
                (root / 'supervisor.c').write_text(content)
                subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(ROOT / 'os/probes'), str(wrapper), '-o', str(root / 'policy')], check=True, capture_output=True)
                result = subprocess.run([str(root / 'policy')], timeout=3)
                self.assertEqual(result.returncode == 0, variant == 'candidate', variant)


if __name__ == '__main__':
    unittest.main()
