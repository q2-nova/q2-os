"""Actual pidfd binding helper controls. Owned processes only; no device."""
from pathlib import Path
import os
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'os/platform/charge-process.c'
DEATH = r'''#include <assert.h>
#include <sys/wait.h>
int main(void) {
    alarm(5);
    pid_t child=fork(); assert(child>=0);
    if (!child) { for (;;) pause(); }
    unsigned long long ticks;
    assert(!start_time(child,&ticks));
    char command[8192],*args[64]; int count=0;
    int length=read_proc(child,"cmdline",command,sizeof(command));assert(length>0);
    for (int off=0;off<length;) {
        assert(count<64);args[count++]=command+off;
        char *end=memchr(command+off,0,(size_t)(length-off));assert(end);
        off=(int)(end-command)+1;
    }
    int fd=bind_checked(child,ticks,count,args,NULL);assert(fd>=0);
    assert(!kill(child,SIGTERM));assert(waitpid(child,NULL,0)==child);
    pid_t foreign=fork();assert(foreign>=0);
    if (!foreign) { for (;;) pause(); }
    errno=0;
    assert(syscall(SYS_pidfd_send_signal,fd,SIGTERM,NULL,0)==-1 && errno==ESRCH);
    assert(!kill(foreign,0));
    close(fd);assert(!kill(foreign,SIGTERM));assert(waitpid(foreign,NULL,0)==foreign);
    puts("bound process death returns ESRCH; unrelated process remains alive");return 0;
}
'''

class Process(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = ROOT / 'build/charge-process-tests'
        cls.out.mkdir(parents=True, exist_ok=True)
        cls.helper = cls.out / 'q2-charge-process'
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(SOURCE), '-o', str(cls.helper)], check=True)

    def call(self, *args):
        return subprocess.run([str(self.helper), *map(str, args)], capture_output=True, timeout=3)

    def test_invalid_or_ambiguous_cli_is_refused(self):
        for args in [(), ('term', '1', '1', '--', '/bin/sh'),
                     ('term', '-2', '1', '--', '/bin/sh'),
                     ('term', '999999999999999999999', '1', '--', '/bin/sh'),
                     ('term', '20', '0', '--', '/bin/sh')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.call(*args).returncode, 0)

    def test_exact_args_and_start_binding_refuse_foreign_or_stale_identity(self):
        child = subprocess.Popen(['/bin/sleep', '20'], start_new_session=True)
        try:
            stamp = self.call('stamp', child.pid, '--', '/bin/sleep', '20')
            self.assertEqual(stamp.returncode, 0, stamp.stderr)
            ticks = int(stamp.stdout)
            self.assertNotEqual(self.call('term', child.pid, ticks + 1, '--', '/bin/sleep', '20').returncode, 0)
            self.assertNotEqual(self.call('term', child.pid, ticks, '--', 'q2-charge-service', 'run').returncode, 0)
            self.assertIsNone(child.poll())
            self.assertEqual(self.call('term', child.pid, ticks, '--', '/bin/sleep', '20').returncode, 0)
            child.wait(timeout=3)
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=3)

    def run_harness(self, text, name, prefix=""):
        source = self.out / (name + '.c')
        source.write_text(prefix + '#define main charge_process_cli_main\n#include "' + str(SOURCE) + '"\n#undef main\n' + text)
        executable = self.out / name
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(executable)], check=True)
        child = subprocess.Popen([str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            out, err = child.communicate(timeout=8)
            self.assertEqual(child.returncode, 0, (out, err))
        finally:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            if child.poll() is None: child.wait(timeout=3)

    def test_unsupported_pidfd_fails_closed_without_signal_fallback(self):
        prefix = ('#include <unistd.h>\n#include <sys/syscall.h>\n#include <errno.h>\n#include <assert.h>\n'
                  'static long unavailable(long n, ...) { assert(n == SYS_pidfd_open); errno=ENOSYS; return -1; }\n'
                  '#define syscall unavailable\n')
        self.run_harness('int main(void) { assert(bind_checked(getpid(),1,0,NULL,NULL)<0); return 0; }\n',
                         'unsupported', prefix)

    def test_death_after_bound_validation_cannot_signal_another_process(self):
        self.run_harness(DEATH, 'death-after-bind')

if __name__ == '__main__': unittest.main()
