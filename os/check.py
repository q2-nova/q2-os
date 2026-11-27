#!/usr/bin/env python3
"""Run image acceptance checks. No hardware or external guest networking."""
import argparse
import json
import os
import uuid
from pathlib import Path
import signal
import subprocess
from build import ROOT, WORK, LOCK, sha, kernel_patch_hashes
from compatibility import INPUTS


def run_step(command, run_id):
    environment = {**os.environ, 'Q2_OS_ACCEPTANCE_ID': run_id}
    process = subprocess.Popen([str(part) for part in command], cwd=ROOT, env=environment)
    try:
        if process.wait(): raise RuntimeError('acceptance step failed: ' + str(command))
    except BaseException:
        if process.poll() is None:
            # Let the Python guest harness execute its finally/close path;
            # abruptly killing it leaves its Podman guest running.
            process.send_signal(signal.SIGINT)
            try: process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--suite', choices=['all', 'core', 'audio', 'integration', 'boot-watchdog', 'kexec', 'appliance', 'network', 'bundle'], default='all')
parser.add_argument('--report', default='acceptance.json', help='JSON filename under build/os')
args = parser.parse_args()
if Path(args.report).name != args.report or not args.report.endswith('.json'): parser.error('report must be a JSON filename')

report = {'suite': args.suite, 'passed': False, 'emulation_only': True, 'sources': LOCK,
          'compatibility_inputs_sha256': sha(INPUTS / 'inputs.json'),
          'kernel_local_patches': kernel_patch_hashes(),
          'rootfs_sha256': sha(WORK / 'out/images/rootfs.squashfs'),
          'disk_sha256': sha(WORK / 'out/images/q2-sd.img'), 'steps': []}
core_steps = [
    ('audit.py', []),
    ('test_toolchain.py', []),
    ('test_boot.py', ['--app']),
    ('test_boot.py', ['--app', '--icount']),
    ('test_faults.py', []),
    ('test_dpu.py', []),
    ('test_watchdog_model.py', []),
    ('test_display.py', []),
    ('test_display.py', ['--icount']),
    ('test_boot.py', ['--initramfs', '--kernel', 'build/os/kernel-initramfs/uImage.gz']),
]
audio_steps = [('test_audio.py', options + timing)
               for options in [['--rate', str(rate)] for rate in [44100, 48000, 96000]] + [['--player'], ['--player', '--pause']]
               for timing in [[], ['--icount']]]
integration_steps = [(script, timing) for script in ['test_input.py', 'test_baseline.py']
                     for timing in [[], ['--icount']]] + [('test_kexec_load.py', [])]
boot_steps = [('test_boot_watchdog.py', ['--case', case] + timing)
              for case in ['transfer', 'kill-owner', 'stop-owner', 'kill-recipient', 'no-ack', 'hang']
              for timing in [[], ['--icount']]]
kexec_steps = [('test_kexec.py', ['--mode', mode] + timing)
               for mode in ['owned', 'hang', 'no-owner'] for timing in [[], ['--icount']]]
appliance_steps = [('test_appliance.py',['--fault',case]) for case in ['none','ui','audio','process','spoof','repeat','missing-state','full-state']] + [('test_appliance.py',['--fault','audio','--icount'])]
network_steps = [('test_network.py',options) for options in [[],['--tls'],['--icount']]]
bundle_steps = [('test_bundle.py',['--case',case]+timing) for case in ['good','broken-root','corrupt','interrupted'] for timing in [[],['--icount']]] + [('test_bootstrap_gate.py',[])]
suites = {'core': core_steps, 'audio': audio_steps, 'integration': integration_steps,
          'boot-watchdog': boot_steps, 'kexec': kexec_steps,
          'appliance':appliance_steps, 'network':network_steps, 'bundle':bundle_steps}
steps = sum(suites.values(), []) if args.suite == 'all' else suites[args.suite]
try:
    for script, options in steps:
        before = set((WORK / 'tests').glob('*/result.json'))
        run_id = uuid.uuid4().hex
        step = {'script': script, 'args': options, 'passed': False, 'test_run': run_id}
        report['steps'].append(step)
        try:
            run_step(['python3', ROOT / 'os' / script, *options], run_id)
            step['passed'] = True
        finally:
            step['results'] = {}
            for path in sorted(set((WORK / 'tests').glob('*/result.json')) - before):
                result = json.loads(path.read_text())
                if result.get('test_run') == run_id:
                    step['results'][str(path.relative_to(ROOT))] = result
    report['passed'] = True
finally:
    (WORK / args.report).write_text(json.dumps(report, indent=2) + '\n')
