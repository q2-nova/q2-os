"""The thrash guard's production C policy against recorded Q2 samples.

  python3 os/test_memory_guard.py                      # the tests
  python3 os/test_memory_guard.py --replay BLOCK...    # monitored blocks

--replay runs the policy over the `M` samples of stream.txt in each block
directory of tests/perf/msa_stall_probe.py and prints, per block, the thrash
seconds (samples with 50 or more major faults) and each action with the
thrash seconds before it. It replays the recorded samples as they were: a
kill would have changed what followed.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

# (MemAvailable kB, major faults in the second), one sample a second,
# V0.122 and V0.123, 2026-11-20 (build/hardware of the stall worktree).
HEALTHY = [(31724, 0), (32136, 12), (32124, 55), (31284, 61), (31540, 42), (30732, 48), (23900, 72), (31616, 0)]
M8 = [(18208, 0), (15744, 10), (18544, 17), (21316, 0)] * 5
# stall-v123-m16-sup-01: 16 MB taken soon after boot, no thrash.
M16_EARLY = [(18212, 0), (16064, 1), (15920, 9), (13144, 23), (16424, 4), (16440, 38), (15892, 0), (15200, 11),
             (16376, 21), (16380, 0)]
# stall-prov-m16-k1-b0-01: 16 MB taken, hard stop after 42 s.
M16 = [(11068, 0), (11560, 44), (12072, 66), (11984, 59), (12292, 64), (12252, 99), (11876, 103), (11976, 103),
       (12004, 102), (12352, 135), (12292, 107), (12256, 116), (12044, 110), (11968, 103), (12088, 105),
       (12028, 122), (11924, 115), (11692, 101), (11888, 106), (12108, 107), (12436, 103), (12288, 91),
       (11812, 120), (11856, 120), (12240, 112), (12136, 108), (12008, 113), (12376, 111)]
# stall-v123-m20-guard-01: 20 MB taken, bursts of two or three seconds.
BURSTS = [(14296, 0), (12696, 21), (11796, 17), (13104, 83), (12912, 117), (11944, 21), (12028, 12), (12156, 0),
          (13272, 64), (12996, 61), (12028, 28), (11816, 3), (11360, 0), (12760, 53), (13284, 106), (18520, 66),
          (17332, 0)]

HARNESS = '''#define Q2_MEMORY_GUARD_POLICY_ONLY
#include "memory-guard.c"
int main(int argc, char **argv) {
    struct guard g = {0};
    long long now = 1000;
    for (int i = 1; i + 1 < argc; i += 2, now += 1000)
        printf("%d", guard_step(&g, atol(argv[i]), atol(argv[i + 1]), now));
    printf(" %ld %ld", field("MemTotal:  118396 kB\\nMemAvailable:   12044 kB\\n", "MemAvailable:"),
           field("pgfault 9\\npgmajfault 4711\\n", "pgmajfault "));
    const char *swap = "pgmajfault 4711\\npswpin 4000\\npswpout 9000\\n";
    printf(" %ld %ld\\n", file_faults(field(swap, "pgmajfault "), field(swap, "pswpin ")),
           file_faults(4711, field("pgmajfault 4711\\n", "pswpin ")));
    return 0;
}
'''


def build(directory):
    root = Path(directory)
    (root / 'harness.c').write_text(HARNESS)
    exe = root / 'harness'
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(ROOT / 'os/probes'),
                    str(root / 'harness.c'), '-o', str(exe)], check=True, capture_output=True)
    return exe


def run(exe, samples):
    arguments = [str(value) for sample in samples for value in sample]
    out = subprocess.run([str(exe), *arguments], check=True, capture_output=True, text=True, timeout=5)
    return out.stdout.split()


class MemoryGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / 'build/perf'
        base.mkdir(parents=True, exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(dir=base)
        cls.exe = build(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def actions(self, samples):
        return run(self.exe, samples)[0] if samples else ''

    def test_healthy_states_do_nothing(self):
        for samples in (HEALTHY * 20, M8, M16_EARLY * 5):
            self.assertEqual(self.actions(samples), '0' * len(samples))

    def test_the_recorded_thrash_climbs_to_a_reboot_within_22_seconds(self):
        actions = self.actions(M16)
        # 44 then 66: two thrash seconds; tailscaled goes in the second.
        self.assertEqual(actions.index('1'), 2)
        # 5 s pause, then the 4th thrash second in a row: the OOM killer.
        self.assertEqual(actions.index('2') - actions.index('1'), 5 + 3)
        # 6 s pause, then the 5th in a row: the orderly reboot.
        self.assertEqual(actions.index('3') - actions.index('2'), 6 + 4)
        self.assertLessEqual(actions.index('3'), 22)
        self.assertNotIn('4', actions)   # 30 s for the reboot to happen

    def test_the_forced_reboot_follows_a_reboot_that_did_not_happen(self):
        actions = self.actions([(12000, 110)] * 80)
        self.assertEqual([actions.count(step) for step in '1234'], [1, 1, 1, 1])
        self.assertEqual(actions.index('4') - actions.index('3'), 30 + 4)

    def test_short_bursts_cost_tailscaled_only(self):
        actions = self.actions(BURSTS)
        self.assertEqual(actions.count('1'), 1)
        self.assertEqual(actions.strip('01'), '')

    def test_sixty_calm_seconds_start_again_at_the_first_step(self):
        burst, calm = [(12000, 110)] * 2, [(12000, 0)]
        self.assertEqual(self.actions(burst + calm * 59 + [(12000, 110)] * 4).count('2'), 1)
        again = self.actions(burst + calm * 61 + [(12000, 110)] * 4)
        self.assertEqual(again.count('1'), 2)
        self.assertNotIn('2', again)

    def test_an_interrupted_streak_starts_again(self):
        self.assertEqual(self.actions([(12000, 110), (12000, 10), (12000, 110), (21000, 110), (12000, 110)]), '00000')

    def test_the_reboot_step_does_not_rely_on_pid_1(self):
        # The installed PID 1 is a shell: BusyBox reboot without -f signals it
        # and nothing happens. The guard syncs and calls reboot(2) itself.
        source = (ROOT / 'os/probes/memory-guard.c').read_text()
        self.assertNotIn('execl("/sbin/reboot"', source)
        self.assertIn('reboot(RB_AUTOBOOT);', source)
        self.assertLess(source.index('sync();'), source.index('reboot(RB_AUTOBOOT);'))

    def test_proc_fields(self):
        self.assertEqual(run(self.exe, [])[:2], ['12044', '4711'])

    def test_swap_ins_are_not_thrash(self):
        # zram swap-ins are major faults too; the guard counts the rest. A
        # kernel without the pswpin line counts every major fault.
        self.assertEqual(run(self.exe, [])[2:], ['711', '4711'])


def replay(blocks):
    with tempfile.TemporaryDirectory() as directory:
        exe = build(directory)
        for block in map(Path, blocks):
            stream = block / 'stream.txt'
            if not stream.is_file():
                continue
            samples = []
            for line in stream.read_text().splitlines():
                parts = line.split()
                if len(parts) > 2 and parts[1] == 'M':
                    values = dict(part.split('=', 1) for part in parts[2:] if '=' in part)
                    if 'maj' in values and 'avail' in values:
                        samples.append((int(values['avail']), int(values['maj'])))
            actions = run(exe, samples)[0] if samples else ''
            thrash = [sum(1 for _, maj in samples[:i + 1] if maj >= 50) for i in range(len(samples))]
            taken = [f'step {a} after {thrash[i]} thrash s' for i, a in enumerate(actions) if a != '0']
            print(f'{block.name}: {len(samples)} samples, {thrash[-1] if thrash else 0} thrash s; '
                  + ('; '.join(taken) or 'no action'))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--replay':
        replay(sys.argv[2:])
    else:
        unittest.main()
