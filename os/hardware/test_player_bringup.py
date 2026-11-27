"""Module choice uses generated DT compatibles; never invokes real bring-up."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=(ROOT/'os/hardware/player-bringup').read_text()
START=SCRIPT.index('clicker_module() {')
FUNCTION=SCRIPT[START:SCRIPT.index('\n}',START)+2]

class ClickerChoiceTest(unittest.TestCase):
    def test_legacy_missing_and_explicit_dma(self):
        base=ROOT/'build/pwm-audio/helper-tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path=Path(directory)/'compatible'
            for content,expected in [(None,'pwm-beeper'),(b'pwm-beeper\0','pwm-beeper'),
                                     (b'ingenic,x2000-pwm-audio\0','snd-x2000-pwm-audio'),
                                     (b'other-device\0','pwm-beeper'),
                                     (b'ingenic,x2000-pwm-audio\0pwm-beeper\0','snd-x2000-pwm-audio')]:
                with self.subTest(content=content):
                    if content is None:path.unlink(missing_ok=True)
                    else:path.write_bytes(content)
                    result=subprocess.run(['sh','-c',FUNCTION+'\nclicker_module "$1"','sh',str(path)],check=True,capture_output=True,text=True)
                    self.assertEqual(result.stdout.strip(),expected)

    def test_ordered_loads_keep_one_complete_exit_snapshot_even_on_failure(self):
        start=SCRIPT.index('load() {')
        load=SCRIPT[start:SCRIPT.index('\n}',start)+2]
        fail=next(line for line in SCRIPT.splitlines() if line.startswith('fail() {'))
        base=ROOT/'build/boot-times/bringup-tests';base.mkdir(parents=True,exist_ok=True)
        for failed in ['', 'second']:
            with self.subTest(failed=failed), tempfile.TemporaryDirectory(dir=base) as directory:
                path=Path(directory)
                script=fail+'\n'+load+'''
log=$1/log
events=$1/events
snapshot=$1/snapshot
failed=$2
bootmark() { :; }
modprobe() { echo "$1" >> "$events"; [ "$1" != "$failed" ]; }
dmesg() { echo snapshot >> "$events"; echo generated-complete-boot-log; }
trap 'dmesg > "$snapshot"' EXIT
load first
load second
load third
'''
                result=subprocess.run(['sh','-c',script,'sh',str(path),failed],capture_output=True,text=True)
                self.assertEqual(result.returncode, 1 if failed else 0)
                self.assertEqual((path/'snapshot').read_text(),'generated-complete-boot-log\n')
                self.assertEqual((path/'events').read_text().splitlines(),
                    ['first','second','snapshot'] if failed else ['first','second','third','snapshot'])
                for module in ['first','second'] if failed else ['first','second','third']:
                    self.assertIn('Loading '+module, result.stdout)
                    self.assertIn('Loading '+module, (path/'log').read_text())
                if failed:self.assertIn('Module failed: second',result.stderr)

if __name__=='__main__':unittest.main()
