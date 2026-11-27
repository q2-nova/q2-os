#!/usr/bin/env python3
"""SPL/NAND root qualification on sanitized disposable QEMU fixtures only."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

_mux_spec=importlib.util.spec_from_file_location('q2_install_ssh_mux',Path(__file__).with_name('ssh_mux.py'))
ssh_mux=importlib.util.module_from_spec(_mux_spec)
_mux_spec.loader.exec_module(ssh_mux)
_userdata_spec=importlib.util.spec_from_file_location('q2_generated_userdata',Path(__file__).with_name('generated_userdata.py'))
generated_userdata=importlib.util.module_from_spec(_userdata_spec)
_userdata_spec.loader.exec_module(generated_userdata)
SOURCE_ROOT = Path(__file__).resolve().parents[2]

def require_admitted_telemetry(console, required):
    # This terminal refusal cannot become a healthy classified player later in
    # the same boot. Preserve the console and retire now, not after five minutes.
    if required and 'Q2_TELEMETRY_FIXTURE_REFUSED' in console.splitlines():
        raise RuntimeError('generated preboot telemetry fixture refused')
# Worktree experiments share the repository's ignored generated fixture store.
ROOT = Path(subprocess.check_output(['git', '-C', SOURCE_ROOT, 'rev-parse',
    '--path-format=absolute', '--git-common-dir'], text=True).strip()).parent
def owned_fixture_path(path):
    path = path.resolve()
    return (path.is_relative_to(ROOT/'build') or
            (SOURCE_ROOT.is_relative_to(ROOT) and path.is_relative_to(SOURCE_ROOT/'build')))


PAGE, STRIDE, ERASE = 2048, 2112, 131072

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def raw(start): return start // PAGE * STRIDE

def retire_boot_container(proc, tag):
    """Bound the owned container stop, frontend reap and final absence check."""
    def engine(args, timeout):
        try:
            return subprocess.run(['podman', *args], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return 'timeout'

    def reap(timeout):
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return 'timeout'

    record = {'stop': engine(['stop', '--time', '1', tag], 60), 'process': reap(30)}
    if record['stop'] != 0 or record['process'] == 'timeout':
        record['remove'] = engine(['rm', '--force', '--ignore', tag], 60)
        record['process'] = reap(60)
    record['exists'] = engine(['container', 'exists', tag], 30)
    record['passed'] = record['exists'] == 1 and record['process'] != 'timeout'
    return record


def retain_codec_audit(out, cycle):
    """Bounded numeric model state, never a physical or analog observation."""
    with socket.socket(socket.AF_UNIX) as control:
        control.settimeout(3)
        control.connect(str(out/'ctl.sock'))
        control.sendall(b'dac\n')
        data = b''
        while not data.endswith(b'\n'):
            block = control.recv(4096)
            if not block or len(data)+len(block)>262144:
                raise ValueError('incomplete or oversized codec audit')
            data += block
    if not data.startswith(b'ok '):
        raise ValueError('codec audit control failed')
    decoded = json.loads(data[3:])
    if not isinstance(decoded,list) or len(decoded)!=4:
        raise ValueError('four generated codec endpoints required')
    path = out/f'codec-audit-{cycle}.json'
    with path.open('x') as stream: json.dump(decoded,stream,indent=2)
    return dict(path=path.name,sha256=sha(path),bytes=path.stat().st_size)

MTDSKIP_QUERY = r"dmesg | sed -n 's/^.*mtdskip: rootfs: \([0-9][0-9]*\) good eraseblocks, \([0-9][0-9]*\) skipped$/Q2_MTDSKIP_PROOF \1 \2/p'; "

def skipbad_proof(text, expected, numeric=False):
    """One actual rootfs mapping, from UART or filtered guest RAM printk."""
    pattern = (r'^Q2_MTDSKIP_PROOF ([0-9]+) ([0-9]+)$' if numeric else
               r'mtdskip: rootfs: ([0-9]+) good eraseblocks, ([0-9]+) skipped$')
    rows = [match.groups() for line in text.splitlines()
            if (match := re.search(pattern,line))]
    if not rows:return None
    if len(rows)!=1:raise ValueError('ambiguous rootfs bad-block scan proof')
    good,skipped=map(int,rows[0])
    if good+skipped!=600 or skipped!=expected:
        raise ValueError('rootfs bad-block scan mismatch')
    return dict(good_eraseblocks=good,skipped_eraseblocks=skipped)

def combine_skipbad_proof(console,ram,expected):
    uart=skipbad_proof(console,expected)
    memory=skipbad_proof(ram,expected,numeric=True)
    if uart is None and memory is None:raise ValueError('rootfs bad-block scan proof unavailable')
    if uart is not None and memory is not None and uart!=memory:
        raise ValueError('rootfs bad-block scan sources disagree')
    return dict(source='console-and-ram' if uart and memory else 'console' if uart else 'guest-ram-printk',
                **(uart or memory))

def guest_evidence_paths(out, cycle, phase=None):
    if type(cycle) is not int or cycle < 0 or (phase is not None and
            (type(phase) is not int or not 0 <= phase <= 2)):
        raise ValueError('bounded guest evidence identity required')
    suffix='' if phase is None else f'-phase-{phase}'
    return dict(stdout=out/f'guest-script-{cycle}{suffix}.log',
                stderr=out/f'guest-script-{cycle}{suffix}.stderr')


def retain_guest_evidence(row, paths):
    row['logs']={name:dict(path=path.name,sha256=sha(path),bytes=path.stat().st_size)
                 for name,path in paths.items() if path.is_file()}


def read_guest_evidence(out, row, stream='stdout'):
    """Consume producer-recorded paths and hashes, never infer phase filenames."""
    entry=row.get('logs',{}).get(stream,{})
    name=entry.get('path','')
    if stream not in ('stdout','stderr') or not re.fullmatch(
            r'guest-script-[0-9]+(?:-phase-[0-2])?\.(?:log|stderr)',name):
        raise ValueError('missing or unsafe producer evidence path')
    path=out/name
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular generated evidence file required')
    if path.stat().st_size!=entry.get('bytes') or sha(path)!=entry.get('sha256'):
        raise ValueError('guest evidence changed')
    return path.read_bytes()


def read_transition_evidence(out, row):
    """Consume only the exact producer-bound reset observation stream."""
    entry=row.get('transition_evidence',{})
    name=entry.get('path','')
    if not re.fullmatch(r'warm-probe-[0-9]+-[0-2]\.jsonl',name):
        raise ValueError('missing or unsafe transition evidence path')
    path=out/name
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=entry.get('bytes') or sha(path)!=entry.get('sha256'):
        raise ValueError('transition evidence changed')
    return path.read_bytes()


def cpm_debug_args(out, cycle, container_path):
    if type(cycle) is not int or cycle not in (0,1):
        raise ValueError('bounded CPM trace cycle required')
    path=out/f'qemu-cpm-{cycle}.log'
    if path.exists() or path.is_symlink():
        raise ValueError('fresh CPM trace path required')
    return ['-d','unimp','-D',container_path(path)],path


def read_cpm_debug_evidence(out, row):
    entry=row.get('cpm_trace',{})
    name=entry.get('path','')
    if not re.fullmatch(r'qemu-cpm-[01]\.log',name):
        raise ValueError('producer CPM trace receipt required')
    path=out/name
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular CPM trace required')
    if path.stat().st_size!=entry.get('bytes') or sha(path)!=entry.get('sha256'):
        raise ValueError('CPM trace changed')
    return path.read_bytes()


def run_guest_script(ssh, script, out, cycle, timeout, host_grace=10, phase=None):
    """Stream owned evidence even if SSH/guest execution is killed or times out."""
    paths=guest_evidence_paths(out,cycle,phase)
    with paths['stdout'].open('xb') as stdout, paths['stderr'].open('xb') as stderr:
        return subprocess.run(ssh + ['timeout', str(timeout), 'sh', '-s'], input=script,
            stdout=stdout, stderr=stderr, timeout=timeout + host_grace)

def telemetry_context(ssh, out, cycle, admission, *, final=False, records=None):
    """ACK actual process context through the same hashed numeric producer."""
    nonce=uuid.uuid4().hex
    script=('set -eu\n/usr/bin/q2nd --telemetry-context-reload '+nonce+'\n'
            'pid=$(cat /run/q2nd/player.pid)\n'
            'start=$(awk \'{print $22}\' /proc/$pid/stat)\n'
            'printf "Q2_TELEMETRY_PROCESS %s %s\\n" "$pid" "$start"\n').encode()
    number=100+cycle*2+int(final);paths=guest_evidence_paths(out,number)
    row=dict(passed=False,final=final,script_sha256=hashlib.sha256(script).hexdigest())
    if records is not None:records.append(row)
    try:
        result=run_guest_script(ssh,script,out,number,25)
        row['returncode']=result.returncode
    finally:retain_guest_evidence(row,paths)
    raw=read_guest_evidence(out,row);err=read_guest_evidence(out,row,'stderr')
    if result.returncode or err or len(raw)>2048:raise ValueError('generated boot telemetry ACK unavailable')
    lines=raw.decode('ascii').splitlines()
    if len(lines)!=2:raise ValueError('generated boot telemetry ACK inventory')
    ack=json.loads(lines[0]);match=re.fullmatch(r'Q2_TELEMETRY_PROCESS ([1-9][0-9]*) ([1-9][0-9]*)',lines[1])
    if not match:raise ValueError('generated boot telemetry process witness')
    generated_userdata.telemetry_overlay.validate_ack(ack,nonce,tuple(map(int,match.groups())))
    if (ack['run_id']!=admission['run_id'] or ack['stream']!='automated-test'
        or ack['labels']!=['automated-test'] or not ack['enabled'] or not ack['overlay_present']):
        raise ValueError('first-player generated telemetry context differs')
    row.update(passed=True,ack=ack)
    return row


def capture_guest_failure(ssh, out, cycle):
    """Bounded disposable-guest evidence; never owner logs or a masked failure."""
    path=out/f'guest-failure-{cycle}.log'
    record=dict(guest_timeout_s=8,
                host_timeout_s=10,scope='generated guest public stages/task states and bounded generated-only profile/bringup; never real owner logs')
    created=False
    try:
        script=Path(__file__).with_name('guest_failure.sh').read_bytes()
        record['script_sha256']=hashlib.sha256(script).hexdigest()
        with path.open('xb') as output:
            created=True
            check=subprocess.run(ssh+['timeout','8','sh','-s'],input=script,
                stdout=output,stderr=subprocess.DEVNULL,timeout=10)
        record.update(status='collected' if check.returncode==0 else 'failed',exit_code=check.returncode)
    except subprocess.TimeoutExpired:
        record.update(status='timed-out',exit_code=None)
    except OSError as error:
        record.update(status='unavailable',error_type=type(error).__name__)
    if created and path.is_file():
        record.update(log=path.name,sha256=sha(path),bytes=path.stat().st_size)
    return record

def capture_readiness_failure(ssh,out,cycle):
    if ssh is None:return dict(status='not-available',reason='generated-ssh-unavailable')
    try:record=capture_guest_failure(ssh,out,cycle)
    except Exception as error:record=dict(status='failed',error_type=type(error).__name__)
    record['trigger']='root-readiness-timeout'
    return record

def raise_readiness_timeout(ssh,out,cycle,result):
    result['readiness_failure_snapshot']=capture_readiness_failure(ssh,out,cycle)
    raise TimeoutError('root readiness timeout')

def run_guest_phases(ssh, scripts, out, cycle, timeout, records, persist, host_grace=10, after_phase=None, energy=None):
    """At most three independently bounded phases; stop on the first failure."""
    if not 1 <= len(scripts) <= 3 or not 1 <= timeout <= 240:
        raise ValueError('one to three phases, each bounded 1–240 seconds')
    for phase,script in enumerate(scripts):
        paths=guest_evidence_paths(out,cycle,phase if len(scripts)>1 else None)
        row=dict(id=f'{cycle}-{phase}',cycle=cycle,phase=phase,status='requested',
            script_sha256=hashlib.sha256(script).hexdigest(),timeout_s=timeout)
        records.append(row);persist()
        began=time.monotonic()
        try:
            if energy:
                row['modeled_energy'] = dict(start=energy.snapshot(out/'ctl.sock'))
            check=run_guest_script(ssh,script,out,cycle,timeout,host_grace,
                phase=phase if len(scripts)>1 else None)
            if energy and check.returncode == 0:
                capture = row['modeled_energy']
                capture['end'] = energy.snapshot(out/'ctl.sock')
                capture['summary'] = energy.window(capture['start'], capture['end'])
        except Exception as error:
            row.update(status='failed',error_type=type(error).__name__,elapsed_seconds=time.monotonic()-began)
            retain_guest_evidence(row,paths)
            persist();raise
        row.update(status='succeeded' if check.returncode==0 else 'failed',
            exit_code=check.returncode,elapsed_seconds=time.monotonic()-began)
        retain_guest_evidence(row,paths)
        persist()
        if check.returncode:return check
        if after_phase is not None and phase + 1 < len(scripts):
            row['transition_status']='requested';persist()
            try:
                row['transition']=after_phase(phase)
                row['transition_status']='succeeded';persist()
            except Exception as error:
                row.update(status='failed',transition_status='failed',error_type=type(error).__name__)
                persist();raise
    return check

def warm_reboot(ssh, process, fixture_tag, timeout=240, evidence=None):
    """Reset one disposable guest without replacing its emulator/RAM instance."""
    if not re.fullmatch(r'install-boot-[0-9a-f]{8}',fixture_tag):
        raise ValueError('generated installation fixture required')
    if process.poll() is not None:raise RuntimeError('emulator exited before warm reset')
    probe=('set -u;boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || :);'
        'state=$(cat /run/q2/state-status 2>/dev/null || :);'
        'p=$(cat /run/q2nd/player.pid 2>/dev/null || :);'
        'h=$(cat /run/q2nd/healthy 2>/dev/null || :);'
        'if test "$(cat /state/.install-fixture 2>/dev/null)" = '+fixture_tag+
        ' && test "$state" = ready && test "$(cat /sys/devices/system/cpu/online 2>/dev/null)" = 0-1'
        ' && test -n "$p" && test "$p" = "$h" && kill -0 "$p" 2>/dev/null; then echo "$boot"; else '
        'read up idle < /proc/uptime; '
        'printf "Q2_WARM_STATUS %s %s state=%s player=%s healthy=%s supervisor=%s launcher=%s\\n" "$boot" "$up" "$state" "$p" "$h" "$(cat /run/q2nd/supervisor.pid 2>/dev/null || :)" "$(cat /run/q2nd/launcher.pid 2>/dev/null || :)"; '
        'grep -E "^Q2_(SUPERVISOR|PLAYER_HEALTH|APP_PREPARATION)" /run/q2nd/supervisor.log 2>/dev/null | tail -4 || :; '
        'grep -E " (app-start|config-ready|fonts-load-submitted|ui-ready|first-frame)$" /run/q2nd/boot-times 2>/dev/null | tail -8 || :; '
        'grep -E " (app-prepare-begin|app-prepare-complete|player-launch-requested)$" /run/q2nd/service-boot-times 2>/dev/null | tail -8 || :; exit 1; fi')
    if evidence is not None:
        with evidence.open('x'): pass
    def retain(value):
        if evidence is not None:
            with evidence.open('a') as f:f.write(json.dumps(value)+'\n')
    before=subprocess.run(ssh+[probe],capture_output=True,text=True,timeout=20,check=True).stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',before):
        raise ValueError('initial boot identity missing')
    started=time.monotonic()
    request=subprocess.run(ssh+['set -eu;test "$(cat /state/.install-fixture)" = '+fixture_tag+
        ';sync;nohup reboot -f >/dev/null 2>&1 </dev/null & echo RESET_REQUESTED'],
        capture_output=True,text=True,timeout=20,check=True)
    if request.stdout.strip()!='RESET_REQUESTED':raise ValueError('reset acknowledgement missing')
    while time.monotonic()-started<timeout:
        if process.poll() is not None:raise RuntimeError('emulator exited during warm reset')
        try:
            # Match cold-boot SSH admission: MIPS-emulated key exchange can
            # exceed five seconds even when the guest/transport are healthy.
            # Keep each attempt and the complete transition bounded.
            remaining=timeout-(time.monotonic()-started)
            if remaining<=0:break
            reply=subprocess.run(ssh+[probe],capture_output=True,text=True,timeout=min(30,remaining))
            retain(dict(seconds=time.monotonic()-started,
                    exit_code=reply.returncode,stdout=reply.stdout,stderr=getattr(reply,'stderr','')))
            after=reply.stdout.strip()
            if (reply.returncode==0 and after!=before and
                re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',after)):
                return dict(passed=True,before_boot=before,after_boot=after,
                    same_emulator_pid=process.pid,seconds=time.monotonic()-started,
                    scope='guest kernel restart through retained same-process QEMU RAM; BootROM is modeled')
        except subprocess.TimeoutExpired as exc:
            retain(dict(seconds=time.monotonic()-started,
                    timeout=True,stdout=(exc.stdout or b'').decode(errors='replace'),
                    stderr=(exc.stderr or b'').decode(errors='replace')))
        time.sleep(.25)
    raise TimeoutError('warm reset did not return a new healthy guest boot')

def populate(nand, start, limit, data, bad=()):
    if len(data) > limit - len(bad) * ERASE: raise ValueError('payload exceeds good blocks')
    with nand.open('r+b') as f:
        f.seek(raw(start)); f.write(b'\xff' * raw(limit))
        offset = 0
        for block in range(limit // ERASE):
            if block in bad:
                for page in (0, 1):
                    f.seek(raw(start + block * ERASE + page * PAGE) + PAGE)
                    f.write(b'\0')
                continue
            piece = data[offset:offset + ERASE]
            if not piece: continue
            offset += len(piece)
            for page in range(0, len(piece), PAGE):
                f.seek(raw(start + block * ERASE + page))
                f.write(piece[page:page + PAGE].ljust(PAGE, b'\xff'))
        assert offset == len(data)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-nand', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--output-root', type=Path, default=ROOT/'build/os/install-review',
                   help='existing canonical build directory for disposable NAND, SD and receipts')
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--out', type=Path, help='fresh generated receipt directory, with its QEMU control socket')
    p.add_argument('--nand-ds35q1ga-ecc-read-us', type=int, choices=range(25, 71), metavar='25..70',
                   help='opt-in effective ECC-on fixture delay in microseconds; requires DS35Q1GA and modeled energy')
    p.add_argument('--bad-blocks', default='', help='comma-separated rootfs-relative eraseblocks')
    p.add_argument('--corrupt-root', action='store_true')
    p.add_argument('--userdata-image', type=Path, help='explicit generated UBI fixture, never owner data')
    cards = p.add_mutually_exclusive_group()
    cards.add_argument('--generated-playback-card', action='store_true',
                   help='attach a disposable generated FAT32 card with an offline saved queue')
    cards.add_argument('--generated-playback-flac-card', action='store_true',
                       help='attach a disposable FAT32 card with three generated FLAC tracks')
    cards.add_argument('--generated-direct-read-card', action='store_true',
                       help='attach a disposable empty exFAT card for generated direct reads')
    p.add_argument('--nand-ds35q1ga', action='store_true',
                   help='model the measured 128 MiB one-plane NAND variant')
    p.add_argument('--boots', type=int, choices=(1, 2), default=1)
    p.add_argument('--require-player', action='store_true', help='require live supervised application health after root/state boot')
    p.add_argument('--require-charge-control', action='store_true', help='exercise actual AXP2101 charge sysfs on the generated guest')
    p.add_argument('--timing', action='store_true', help='record host intervals and generated guest boot milestones; no physical timing claim')
    p.add_argument('--require-single-boot', action='store_true',
                   help='reject guest resets during each cold emulator cycle')
    p.add_argument('--require-loopback', action='store_true', help='require UP loopback and 127.0.0.1/8 in the installed startup fixture')
    p.add_argument('--key', type=Path, help='generated fixture SSH key; enables real guest SSH checks')
    p.add_argument('--host-public-key', type=Path, help='matching generated bootstrap host public key')
    p.add_argument('--guest-script', type=Path, help='owned generated fixture script under build/, run through guest SSH after readiness')
    p.add_argument('--guest-script-extra',type=Path,action='append',default=[],help='up to two following owned phases; each retains the same bounded timeout')
    p.add_argument('--guest-script-timeout', type=int, default=90, help='bounded generated guest script lifetime, 1–240 seconds')
    p.add_argument('--guest-script-host-grace', type=int, default=10, metavar='SECONDS',
                   help='extra host wall time beyond the unchanged guest script limit, 0–360 seconds')
    p.add_argument('--root-readiness-timeout', type=int, metavar='SECONDS',
                   help='outer root and player readiness bound for generated guest-script runs, 1–1200 seconds')
    p.add_argument('--debug-qmp',action='store_true',help='local QMP socket for an owned disposable guest diagnostic')
    p.add_argument('--debug-sd-requests',action='store_true',
                   help='retain command and data-block traces for a generated microSD')
    p.add_argument('--capture-boot-display', action='store_true',
                   help='observe display changes through the owned guest QMP socket during cold boot')
    p.add_argument('--host-cpuset', help='comma-separated CPU IDs for the container QEMU process')
    p.add_argument('--initial-ssh-delay', type=int, default=0,
                   help='delay first boot SSH probe until this many seconds after launch (0–180)')
    p.add_argument('--audio-capture', action='store_true', help='retain per-boot digital WAV/format-aware PCM from the generated guest')
    p.add_argument('--modeled-energy', action='store_true', help='record virtual energy by component for each guest script')
    p.add_argument('--energy-icount-shift', type=int, choices=range(0, 6),
                   help='virtual ns per instruction as 2**shift for energy workloads (default: 1)')
    p.add_argument('--energy-profile', type=Path, help='Q2 model coefficient overrides, frozen with this run')
    p.add_argument('--energy-wifi', choices=('auto', 'off', 'ips', 'lps', 'cam'),
                   help='Wi-Fi scenario during guest phases (default: observe board enable)')
    p.add_argument('--energy-bt', choices=('auto', 'off', 'idle', 'scan', 'connect', 'a2dp'),
                   help='Bluetooth scenario during guest phases (default: observe board enable)')
    p.add_argument('--codec-pdn-stuck', action='store_true', help='generated codec completion fault; requires audio capture')
    p.add_argument('--debug-cpm',action='store_true',help='retain per-boot unimplemented/register attribution log for this generated guest')
    p.add_argument('--debug-netdump',action='store_true',help='record only this isolated generated guest network to build/ for transport diagnosis')
    p.add_argument('--fixture-control-port', type=int,
                   help='forward generated guest TCP 192.168.77.4:8443 to this loopback-only fixture port')
    p.add_argument('--debug-guest-errors',action='store_true',help='retain modeled fault/reset attribution in the owned QEMU stderr log')
    p.add_argument('--warm-reboot-between-phases',action='store_true',help='one disposable same-emulator kernel restart between each guest phase; requires one cold boot and player health')
    p.add_argument('--ssh-multiplex',action='store_true',help='owned short-lived SSH control socket for generated fixtures only')
    a = p.parse_args()
    energy = None
    if (a.energy_wifi or a.energy_bt or a.energy_profile or a.energy_icount_shift is not None) and not a.modeled_energy:
        p.error('energy options require --modeled-energy')
    if a.nand_ds35q1ga_ecc_read_us is not None and not (a.nand_ds35q1ga and a.modeled_energy):
        p.error('DS35Q1GA ECC timing trial requires --nand-ds35q1ga and --modeled-energy')
    if a.modeled_energy:
        if a.energy_icount_shift is None:
            a.energy_icount_shift = 1
        if not a.guest_script:
            p.error('modeled energy requires a bounded guest workload')
        spec = importlib.util.spec_from_file_location('q2_energy', SOURCE_ROOT/'qemu/tools/energy.py')
        energy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(energy)
    if a.codec_pdn_stuck and not a.audio_capture:p.error('codec fault requires retained audio audit')
    if bool(a.key) != bool(a.host_public_key): p.error('supply both generated SSH key arguments')
    if a.ssh_multiplex and not a.key:p.error('SSH reuse requires generated SSH fixture')
    if a.host_cpuset and not re.fullmatch(r'[0-9]{1,4}(?:,[0-9]{1,4}){0,31}', a.host_cpuset):
        p.error('use a list of up to 32 CPU IDs')
    if not 0 <= a.initial_ssh_delay <= 180 or a.initial_ssh_delay and not a.key:
        p.error('initial SSH delay must be 0–180 seconds with generated SSH')
    if a.debug_netdump and not a.key:p.error('network dump requires generated SSH fixture')
    if a.fixture_control_port is not None and (
            not 1024 <= a.fixture_control_port <= 65535 or not a.key or not a.userdata_image or a.corrupt_root):
        p.error('control forwarding requires generated SSH and userdata on a valid root and an unprivileged loopback port')
    if a.debug_guest_errors and a.debug_cpm:p.error('select guest-error or CPM tracing separately')
    if a.require_charge_control and not a.key: p.error('charge-control test requires guest SSH')
    if not 1 <= a.guest_script_timeout <= 240: p.error('guest-script timeout must be 1–240 seconds')
    guest_paths=([a.guest_script]+a.guest_script_extra) if a.guest_script else []
    if not 0 <= a.guest_script_host_grace <= 360 or (a.guest_script_host_grace != 10 and not guest_paths):
        p.error('guest script host grace requires a generated script and 0–360 seconds')
    if a.guest_script_extra and not a.guest_script:p.error('extra phases require a first guest script')
    if a.root_readiness_timeout is not None and (
            not 1 <= a.root_readiness_timeout <= 1200 or not guest_paths or not a.require_player):
        p.error('root readiness override requires --require-player, a generated guest script and 1–1200 seconds')
    if len(guest_paths)>3:p.error('at most three bounded guest phases')
    if a.warm_reboot_between_phases and (len(guest_paths)<2 or a.boots!=1 or not a.require_player):p.error('warm transitions require2–3 phases, one cold boot and player health')
    if a.require_single_boot and a.warm_reboot_between_phases:
        p.error('single-boot admission cannot include requested warm resets')
    if guest_paths and (not a.key or any(not owned_fixture_path(path) for path in guest_paths)):
        p.error('guest script requires generated guest SSH and a build/ path')
    if a.require_loopback and (not a.key or a.corrupt_root): p.error('loopback check requires installed-root SSH')
    playback_card = a.generated_playback_card or a.generated_playback_flac_card
    if playback_card and not a.require_player:
        p.error('generated playback card requires --require-player')
    if a.generated_direct_read_card and (not a.require_player or not guest_paths):
        p.error('generated direct-read card requires --require-player and a guest script')
    if a.debug_sd_requests and not (playback_card or a.generated_direct_read_card):
        p.error('SD request tracing requires a generated card')
    if a.require_player and (not a.userdata_image or a.corrupt_root): p.error('player test requires generated userdata and a valid root')
    if a.userdata_image and (not a.key or not owned_fixture_path(a.userdata_image)):
        p.error('generated userdata fixture requires SSH checks and a build/ path')
    telemetry_admission=generated_userdata.admit(a.userdata_image,a.candidate) if a.userdata_image and not a.corrupt_root else None
    # Freeze exactly the bytes that will be sent after boot; an edited fixture
    # cannot silently diverge from the script SHA recorded in this receipt.
    guest_scripts = [path.read_bytes() for path in guest_paths]
    for path in (a.base_nand, a.candidate, a.qemu):
        if not owned_fixture_path(path): p.error('fixtures must be under build/')
    if a.base_nand.stat().st_size != 131072 * STRIDE: p.error('not full-chip data+OOB NAND')
    with a.base_nand.open('rb') as f:
        for start, end in ((0x6400000, 0x6440000), (0x6440000, 0x7d00000)):
            f.seek(raw(start))
            if any(x != 255 for x in f.read(raw(end-start))): p.error('identity/selector/userdata must be erased')
    bad = sorted(set(int(x) for x in a.bad_blocks.split(',') if x))
    if any(x < 0 or x >= 600 for x in bad): p.error('bad block index outside rootfs')
    output_root = a.output_root.absolute()
    if output_root != output_root.resolve() or not output_root.is_relative_to(ROOT/'build'):
        p.error('use a canonical output root under build/')
    if a.output_root != p.get_default('output_root'):
        if not output_root.is_dir() or output_root.stat().st_uid != os.geteuid():
            p.error('explicit output root must already exist and be owned by the caller')
    tag = 'install-boot-' + uuid.uuid4().hex[:8]
    if a.out is not None:
        if a.output_root != p.get_default('output_root'):
            p.error('--out and --output-root are mutually exclusive')
        out = a.out.absolute()
        if out != out.resolve() or not owned_fixture_path(out):
            p.error('use a canonical output path under build/')
    else:
        out = output_root / tag
    out.mkdir(parents=True, exist_ok=False)
    card_image = None
    if playback_card or a.generated_direct_read_card:
        spec = importlib.util.spec_from_file_location(
            'q2_generated_playback_card', Path(__file__).with_name('generated_playback_card.py'))
        playback = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(playback)
        if playback_card:
            card_name = 'playback-card'
            card_record = playback.create(out / card_name, flac=a.generated_playback_flac_card)
        else:
            card_name = 'direct-read-card'
            card_record = playback.create_direct_read(out / card_name)
        card_image = out / card_name / card_record['image']
    nand = out / 'nand.img'; shutil.copyfile(a.base_nand, nand)
    populate(nand, 0x100000, 6*1024**2, (a.candidate / 'xImage').read_bytes())
    root = (a.candidate / 'rootfs.squashfs').read_bytes()
    if a.corrupt_root: root = bytes(4096) + root[4096:]
    populate(nand, 0x700000, 75*1024**2, root, bad)
    # These fixtures expose blank userdata: startup must refuse autoformat.
    if a.userdata_image: populate(nand, 0x6440000, 0x18c0000, a.userdata_image.read_bytes())
    protected = [(0,0x100000),(0x5200000,0x6440000 if a.userdata_image else 0x7d00000)]
    def protected_hashes():
        with nand.open('rb') as f:
            result=[]
            for start,end in protected:
                f.seek(raw(start)); result.append(hashlib.sha256(f.read(raw(end-start))).hexdigest())
            return result
    before = protected_hashes()
    container_path = lambda path: '/r/' + str(path.resolve().relative_to(ROOT))
    port = None
    nic = 'none'
    if a.key:
        with socket.socket() as free:
            free.bind(('127.0.0.1', 0)); port = free.getsockname()[1]
        public = a.host_public_key.read_text().strip()
        if not public.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')): p.error('plain host public key required')
        (out/'known_hosts').write_text('q2-install-fixture ' + public + '\n')
        nic = f'user,restrict=on,net=192.168.77.0/24,host=192.168.77.1,hostfwd=tcp:127.0.0.1:{port}-192.168.77.2:22'
        if a.fixture_control_port is not None:
            # Keep .1 as the guest's /30 peer for SSH. The generated control
            # fixture alone supplies a host route to .4 via that peer.
            nic += f',guestfwd=tcp:192.168.77.4:8443-cmd:/usr/bin/socat STDIO TCP4:127.0.0.1:{a.fixture_control_port}'
        if a.debug_netdump:nic=nic.replace('user,','user,id=q2fixture,',1)
    affinity = ['/usr/bin/taskset', '-c', a.host_cpuset] if a.host_cpuset else []
    command = ['podman','run','--rm','--name',tag,'--network','host' if a.key else 'none','--security-opt','label=disable',
       '-v',f'{ROOT}:/r','-w','/r','q2-qemu-build',*affinity,container_path(a.qemu),'-M','q2,boot-source=nand',
       '-display','none','-nographic','-nic',nic,'-monitor','none',
       '-serial','file:'+container_path(out/'console.log'),
       '-serial','unix:'+container_path(out/'shell.sock')+',server=on,wait=off',
       '-chardev','socket,id=q2ctl,path='+container_path(out/'ctl.sock')+',server=on,wait=off',
       '-drive','if=mtd,format=raw,file='+container_path(nand)]
    if a.nand_ds35q1ga:
        command[command.index('-M')+1] += ',nand-ds35q1ga=on'
    if a.nand_ds35q1ga_ecc_read_us is not None:
        command[command.index('-M')+1] += f',nand-ds35q1ga-ecc-read-us={a.nand_ds35q1ga_ecc_read_us}'
    if card_image:
        command.extend(['-drive', 'if=sd,format=raw,snapshot=on,file='+container_path(card_image)])
    if a.energy_icount_shift is not None:
        command += ['-icount', f'shift={a.energy_icount_shift}']
    if a.energy_profile:
        profile = out/'energy-profile.txt'
        with profile.open('xb') as dest:
            dest.write(a.energy_profile.read_bytes())
        command[command.index('-M')+1] += ',power-model=' + container_path(profile)
    if a.debug_qmp or a.capture_boot_display:
        command.extend(['-qmp','unix:'+container_path(out/'qmp.sock')+',server=on,wait=off'])
    if a.capture_boot_display:
        sys.path.insert(0, str(SOURCE_ROOT / 'tests/perf'))
        from cold_boot_display import BootDisplay
    if a.debug_guest_errors:command.extend(['-d','guest_errors'])
    if a.debug_sd_requests:
        for event in ('sdhci_send_command', 'sdcard_read_block', 'sdcard_write_block'):
            command.extend(['-trace', 'enable=' + event])
    if a.debug_netdump:command.extend(['-object','filter-dump,id=q2fixture-dump,netdev=q2fixture,file='+container_path(out/'network.pcap')])
    # Only the exFAT fixture has a different default because it can make one
    # exported-to-local USB return before the supervised-player boot gate.
    ready_timeout_s = a.root_readiness_timeout or (420 if a.generated_direct_read_card else 300)
    result={'hardware':False,'passed':False,'bad_blocks':bad,'corrupt_root':a.corrupt_root,
            'xImage_sha256':sha(a.candidate/'xImage'),'rootfs_sha256':sha(a.candidate/'rootfs.squashfs'),
            'qemu_sha256':sha(a.qemu),'runner_sha256':sha(Path(__file__)),
            'ssh_mux_sha256':sha(Path(ssh_mux.__file__)),'command':command}
    if card_image:
        card_key = 'generated_playback_card' if playback_card else 'generated_direct_read_card'
        result[card_key] = dict(card_record, receipt=f'{card_name}/result.json')
    if a.generated_direct_read_card or a.root_readiness_timeout is not None:
        result['root_readiness_timeout_s'] = ready_timeout_s
    if a.nand_ds35q1ga:
        result['nand_variant'] = 'DS35Q1GA'
    if energy:
        result['energy_conditions'] = dict(base_nand_sha256=sha(a.base_nand),
            userdata_sha256=sha(a.userdata_image) if a.userdata_image else None,
            analyzer_sha256=sha(Path(energy.__file__)), audio_capture=a.audio_capture,
            codec_fault=a.codec_pdn_stuck, warm_reboot=a.warm_reboot_between_phases,
            cpm_trace=a.debug_cpm, netdump=a.debug_netdump, guest_errors=a.debug_guest_errors,
            charge_control=a.require_charge_control, require_player=a.require_player,
            loopback=a.require_loopback, timing=a.timing, wifi=a.energy_wifi or 'auto',
            bluetooth=a.energy_bt or 'auto', icount_shift=a.energy_icount_shift)
        if a.nand_ds35q1ga:
            result['energy_conditions']['nand_variant'] = result['nand_variant']
        if a.nand_ds35q1ga_ecc_read_us is not None:
            result['energy_conditions']['nand_ds35q1ga_ecc_read_us'] = a.nand_ds35q1ga_ecc_read_us
    if a.guest_script:
        result['guest_script_sha256'] = hashlib.sha256(guest_scripts[0]).hexdigest()
        result['guest_scripts_sha256']=[hashlib.sha256(script).hexdigest() for script in guest_scripts]
        result['guest_script_timeout_s'] = a.guest_script_timeout
        if a.guest_script_host_grace != 10:
            result['guest_script_host_grace_s'] = a.guest_script_host_grace
        result['guest_script_timeout_scope']='per phase; at most three phases per boot'
        result['guest_phases']=[]
    result['telemetry_preboot']=telemetry_admission
    result['telemetry_contexts']=[]
    result['boots'] = []
    result['ssh_transport']='multiplex-60s' if a.ssh_multiplex else 'fresh'
    if a.codec_pdn_stuck:
        command += ['-global', 'x2000-cs43131.pdn-stuck=on']
        result['codec_pdn_stuck'] = True
    machine = command[command.index('-M')+1]
    for cycle in range(a.boots):
        if card_image and playback.sha(card_image) != card_record['image_sha256']:
            raise RuntimeError('generated card changed before boot')
        ssh=None
        result['passed'] = False
        console_file = out / f'console-{cycle}.log'
        command[command.index('-serial')+1] = 'file:' + container_path(console_file)
        if a.audio_capture:
            command[command.index('-M')+1] = machine + ',audio-wav=' + container_path(out/f'audio-{cycle}.wav')
        mux_owner=None
        mux_cleanup_failed=False
        diagnostic_log=out/f'qemu-{cycle}.log'
        with ssh_mux.Trace(result), diagnostic_log.open('x') as log:
            launched = time.monotonic()
            timing = {}
            cpm_trace=None;launch_command=command
            if a.debug_cpm:
                debug_args,cpm_trace=cpm_debug_args(out,cycle,container_path)
                launch_command=command+debug_args
                if cycle==0:result['command']=launch_command
                result['debug_cpm']=True
            proc=subprocess.Popen(launch_command,stdout=log,stderr=subprocess.STDOUT)
            display = None
            bad_proof = None
            try:
                if a.capture_boot_display:
                    display = BootDisplay(out, cycle, launched, container_path)
                    display.start()
                marker='Q2_INSTALL_ROOT_FAILED' if a.corrupt_root else 'Q2_INSTALLED_ROOT_READY'
                end=time.monotonic()+ready_timeout_s
                previous_console_poll = launched
                while time.monotonic()<end:
                    console_poll_started = time.monotonic()
                    console=console_file.read_text(errors='replace') if console_file.exists() else ''
                    console_poll_finished = time.monotonic()
                    require_admitted_telemetry(console, bool(telemetry_admission and a.require_player))
                    if a.require_single_boot and (
                            console.count('====into normal====') > 1 or console.count('Linux version') > 1):
                        raise RuntimeError('guest reset during cold emulator cycle')
                    if a.timing:
                        for name, text in [('spl_normal', '====into normal===='), ('kernel', 'Linux version'), ('installed_root', marker)]:
                            if text in console and name not in timing:
                                timing[name] = console_poll_finished - launched
                                timing[name + '_console_window'] = [previous_console_poll - launched, timing[name]]
                    previous_console_poll = console_poll_started
                    if marker in console:
                        if '====into normal====' not in console: raise RuntimeError('missing real SPL normal boot')
                        if not a.corrupt_root and not a.key:
                            bad_proof=combine_skipbad_proof(console,'',len(bad))
                        if a.key:
                            if console_poll_finished - launched < a.initial_ssh_delay:
                                time.sleep(.25)
                                continue
                            ssh = ['ssh','-F','/dev/null','-i',str(a.key.resolve()),'-p',str(port),
                               '-o','BatchMode=yes','-o','IdentityAgent=none','-o','IdentitiesOnly=yes','-o','ConnectTimeout=15',
                                   '-o','HostKeyAlias=q2-install-fixture','-o','StrictHostKeyChecking=yes',
                                   '-o','UserKnownHostsFile='+str(out/'known_hosts'),'root@127.0.0.1']
                            if a.ssh_multiplex:
                                if mux_owner is None:mux_owner=ssh_mux.prepare(out,cycle)
                                ssh=ssh_mux.arguments(ssh,mux_owner)
                            query = 'echo Q2_INSTALL_SSH; cat /sys/devices/system/cpu/online; cat /proc/mtd; cat /run/q2/state-status 2>/dev/null; cat /run/log/state 2>/dev/null; '
                            if a.require_single_boot and a.require_player:
                                query = ('test -s /run/q2nd/player.pid && test -s /run/q2nd/healthy && '
                                    'test "$(cat /run/q2nd/healthy)" = "$(cat /run/q2nd/player.pid)" '
                                    '&& kill -0 "$(cat /run/q2nd/player.pid)" || exit 71; ' + query)
                            if a.userdata_image:
                                query += 'test "$(cat /run/q2/state-status)" = ready && '
                                if cycle == 0: query += 'printf %s ' + tag + ' > /state/.install-fixture && sync && '
                                else: query += 'test "$(cat /state/.install-fixture)" = ' + tag + ' && '
                                query += 'echo Q2_STATE_PERSIST_PASS; '
                            if a.require_player:
                                query += 'test -s /run/q2nd/player.pid && test -s /run/q2nd/healthy && test "$(cat /run/q2nd/healthy)" = "$(cat /run/q2nd/player.pid)" && kill -0 "$(cat /run/q2nd/player.pid)" && echo Q2_PLAYER_HEALTH_PASS; '
                            if a.require_charge_control:
                                query += ('b=/sys/class/power_supply/axp20x-battery; '
                                    'limits=$(cat $b/voltage_max $b/constant_charge_current_max $b/charge_term_current); '
                                    'test -w $b/charge_behaviour && '
                                    'echo inhibit-charge > $b/charge_behaviour && '
                                    'grep -Fq "[inhibit-charge]" $b/charge_behaviour && '
                                    '! (echo force-discharge > $b/charge_behaviour) 2>/dev/null && '
                                    'grep -Fq "[inhibit-charge]" $b/charge_behaviour && '
                                    'echo auto > $b/charge_behaviour && '
                                    'grep -Fq "[auto]" $b/charge_behaviour && '
                                    'test "$limits" = "$(cat $b/voltage_max $b/constant_charge_current_max $b/charge_term_current)" && '
                                    'echo Q2_CHARGE_CONTROL_PASS; ')
                            if a.timing:
                                query += 'echo Q2_BOOT_TIMING_BEGIN; cat /run/q2/boot-times /run/q2nd/boot-times 2>/dev/null; echo Q2_BOOT_TIMING_END; '
                                query += 'read -r uptime idle < /proc/uptime && echo Q2_BOOT_TIMING_UPTIME "$uptime"; '
                            if a.require_loopback:
                                query += 'flags=$(cat /sys/class/net/lo/flags) && test "$((flags & 1))" = 1 && ip -4 addr show dev lo | grep -q "inet 127.0.0.1/8 " && echo Q2_LOOPBACK_PASS; '
                            if not a.corrupt_root:query += MTDSKIP_QUERY
                            query += 'exit 0'
                            ssh_started = time.monotonic()
                            try:
                                check = subprocess.run(ssh+[query],capture_output=True,text=True,timeout=30)
                            except subprocess.TimeoutExpired:
                                time.sleep(.5); continue
                            if check.returncode:
                                if a.require_single_boot and check.returncode == 71:
                                    time.sleep(.5)
                                    continue
                                (out/'ssh-error.log').write_text(check.stderr); time.sleep(.5); continue
                            (out/f'ssh-{cycle}.log').write_text(check.stdout)
                            if a.userdata_image and 'Q2_STATE_PERSIST_PASS' not in check.stdout:
                                raise RuntimeError('state mount/persistence check failed')
                            if 'Q2_INSTALL_SSH' not in check.stdout or '0-1' not in check.stdout:
                                raise RuntimeError('guest SSH/CPU checks failed')
                            if a.require_player and 'Q2_PLAYER_HEALTH_PASS' not in check.stdout:
                                time.sleep(1); continue
                            if not a.corrupt_root:
                                bad_proof=combine_skipbad_proof(console,check.stdout,len(bad))
                            if a.require_charge_control and 'Q2_CHARGE_CONTROL_PASS' not in check.stdout:
                                raise RuntimeError('charge-control guest test failed')
                            result['charge_control'] = bool(a.require_charge_control)
                            if a.require_loopback:
                                if 'Q2_LOOPBACK_PASS' not in check.stdout:
                                    time.sleep(1); continue
                                result['loopback'] = True
                            if a.timing:
                                timing['healthy_ssh'] = time.monotonic() - launched
                                timing['guest'] = check.stdout.split('Q2_BOOT_TIMING_BEGIN\n')[-1].split('Q2_BOOT_TIMING_END')[0].splitlines()
                                uptime = re.search(r'^Q2_BOOT_TIMING_UPTIME ([0-9]+\.[0-9]+)$', check.stdout, re.M)
                                if uptime:
                                    timing['ssh_clock_anchor'] = dict(guest_seconds=float(uptime[1]),
                                        host_window_seconds=[ssh_started - launched, timing['healthy_ssh']])
                            result['ssh']=True
                            result['player_health'] = bool(a.require_player)
                            if telemetry_admission:
                                telemetry_context(ssh,out,cycle,telemetry_admission,records=result['telemetry_contexts'])
                            if a.nand_ds35q1ga_ecc_read_us is not None:
                                nand_info = energy.control(out/'ctl.sock', 'nand info')
                                result['nand_info'] = nand_info
                                if (nand_info['id'], nand_info['pages'], nand_info['ecc_read_us']) != (
                                        0x71, 65536, a.nand_ds35q1ga_ecc_read_us):
                                    raise ValueError('emulator does not report the admitted DS35Q1GA ECC read delay')
                            if a.guest_script:
                                try:
                                    def persist_phase():
                                        temporary=out/'result.phase-new'
                                        temporary.write_text(json.dumps(result,indent=2)+'\n')
                                        temporary.replace(out/'result.json')
                                    def transition(phase):
                                        path=out/f'warm-probe-{cycle}-{phase}.jsonl'
                                        try:return warm_reboot(ssh,proc,tag,evidence=path)
                                        finally:
                                            if path.is_file():
                                                result['guest_phases'][-1]['transition_evidence']=dict(path=path.name,sha256=sha(path),bytes=path.stat().st_size)
                                                persist_phase()
                                    if energy:
                                        for lane, mode in [('wifi', a.energy_wifi), ('bt', a.energy_bt)]:
                                            if mode:
                                                energy.control(out/'ctl.sock', f'radio {lane} {mode}')
                                    check = run_guest_phases(ssh,guest_scripts,out,cycle,a.guest_script_timeout,
                                        result['guest_phases'],persist_phase,host_grace=a.guest_script_host_grace,
                                        after_phase=transition if a.warm_reboot_between_phases else None,energy=energy)
                                except subprocess.TimeoutExpired:
                                    result['guest_script_passed'] = False
                                    result['guest_script_host_timeout'] = True
                                    result['guest_script_exit_code'] = None
                                    raise
                                finally:
                                    if result['guest_phases'] and result['guest_phases'][-1]['status']=='failed':
                                        result['guest_failure_snapshot']=capture_guest_failure(ssh,out,cycle)
                                result['guest_script_passed'] = check.returncode == 0
                                result['guest_script_exit_code'] = check.returncode
                                if check.returncode: raise RuntimeError('generated guest script failed')
                            if telemetry_admission:
                                telemetry_context(ssh,out,cycle,telemetry_admission,final=True,records=result['telemetry_contexts'])
                            if a.timing and a.ssh_multiplex and a.require_single_boot:
                                anchor_start = time.monotonic()
                                anchor = subprocess.run(ssh+['read -r uptime idle < /proc/uptime && echo "$uptime"'],
                                    capture_output=True, text=True, timeout=15, check=True)
                                anchor_end = time.monotonic()
                                if not re.fullmatch(r'[0-9]+\.[0-9]+\n', anchor.stdout):
                                    raise ValueError('invalid connected SSH clock anchor')
                                timing['connected_ssh_clock_anchor'] = dict(guest_seconds=float(anchor.stdout),
                                    host_window_seconds=[anchor_start - launched, anchor_end - launched])
                        result['passed']=True; break
                    if proc.poll() is not None or 'Kernel panic' in console: raise RuntimeError('boot failed')
                    time.sleep(.25)
                else:
                    raise_readiness_timeout(ssh,out,cycle,result)
            finally:
                if mux_owner is not None:
                    try:result.setdefault('ssh_mux_cleanup',[]).append(ssh_mux.cleanup(mux_owner,ssh))
                    except Exception as error:
                        result['ssh_mux_cleanup_error']=type(error).__name__
                        result['passed']=False;mux_cleanup_failed=True
                codec_audit = None
                if a.audio_capture:
                    try: codec_audit = retain_codec_audit(out,cycle)
                    except (OSError,ValueError) as error:
                        codec_audit = dict(unavailable=type(error).__name__)
                display_result = None
                if display is not None:
                    try: display_result = display.finish()
                    except Exception as error: display_result = dict(error=type(error).__name__)
                retirement = retire_boot_container(proc, tag)
                result.setdefault('container_cleanup', []).append(retirement)
                if not retirement['passed']:
                    result['passed'] = False
                if telemetry_admission and retirement['passed']:
                    result['telemetry_scope_end'] = 'disposable-guest-processes-retired; no live production handoff'
                result['protected_unchanged']=before==protected_hashes()
                if not result['protected_unchanged']: result['passed']=False
                if card_image:
                    check_key = 'playback_card_unchanged' if playback_card else 'direct_read_card_unchanged'
                    result[check_key] = playback.sha(card_image) == card_record['image_sha256']
                    if not result[check_key]:
                        result['passed'] = False
                boot_result = {'cycle':cycle,'passed':result['passed']}
                if display_result is not None:
                    boot_result['display'] = display_result
                if a.audio_capture:
                    boot_result['codec_audit'] = codec_audit
                    boot_result['audio_capture'] = [dict(path=path.name,sha256=sha(path),bytes=path.stat().st_size)
                        for path in (out/f'audio-{cycle}.wav',out/f'audio-{cycle}.wav.pcm') if path.is_file()]
                if a.debug_cpm:
                    boot_result['command']=launch_command
                    if cpm_trace.is_file():
                        boot_result['cpm_trace']=dict(path=cpm_trace.name,sha256=sha(cpm_trace),bytes=cpm_trace.stat().st_size)
                if a.debug_sd_requests:
                    boot_result['sd_request_trace']=dict(path=diagnostic_log.name,
                        sha256=sha(diagnostic_log),bytes=diagnostic_log.stat().st_size)
                if not a.corrupt_root:boot_result['bad_block_proof']=bad_proof
                if a.timing:
                    boot_result['timing'] = timing
                    result['timing_scope'] = 'QEMU process-launch wall intervals include container overhead; guest uptime milestones are emulated, not physical'
                result['boots'].append(boot_result)
                (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
                print(out/'result.json')
            if mux_cleanup_failed:raise RuntimeError('owned SSH cleanup failed')
    if not result['passed']: raise SystemExit(1)

if __name__=='__main__': main()
