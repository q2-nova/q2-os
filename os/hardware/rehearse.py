#!/usr/bin/env python3
"""Boot the exact executable image through the real SPL on a disposable NAND copy.
Uses only a sanitized mkflash.py fixture (identity/userdata erased). No hardware.
Without update options this is direct fixture placement. --recovery-package
exercises stock recovery, and --update-package starts with the on-device updater.
"""
import argparse
import base64
import os
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import time
import uuid

# The owned-phase adapter executes this file with runpy.run_path, which does
# not add this directory to sys.path. Bind the sibling explicitly so execution
# also cannot pick up an unrelated cached module named "package".
_package_spec = importlib.util.spec_from_file_location('q2_spl_package', Path(__file__).with_name('package.py'))
_package = importlib.util.module_from_spec(_package_spec)
_package_spec.loader.exec_module(_package)
uimage, read_package, stage = _package.uimage, _package.read_package, _package.stage
KERNEL, ROOTFS = _package.KERNEL, _package.ROOTFS

ROOT = Path(__file__).resolve().parents[2]
PAGE, STRIDE = 2048, 2112


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def retire_container(cidfile, name, run=subprocess.run):
    """Only a captured immutable ID authorizes removal; never remove by name."""
    def exists(identity):
        checked = run(['podman', 'container', 'exists', identity], timeout=10,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if checked.returncode not in (0, 1):
            raise RuntimeError('container existence check failed')
        return checked.returncode == 0

    if cidfile.is_symlink():
        raise RuntimeError('refusing symlink container ID receipt')
    if not cidfile.exists():
        if exists(name):
            raise RuntimeError('container present without captured ID; outer owned cleanup required')
        return {'absent': True, 'container_id': None}
    if not cidfile.is_file() or cidfile.stat().st_size > 65:
        raise RuntimeError('invalid container ID receipt')
    identity = cidfile.read_text().strip()
    if not re.fullmatch('[0-9a-f]{64}', identity):
        raise RuntimeError('invalid captured container ID')
    removed = run(['podman', 'rm', '-f', identity], timeout=15,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # --rm may win this race. A failed command is reconciled only by absence.
    if exists(identity):
        raise RuntimeError('captured container remains after removal')
    return {'absent': True, 'container_id': identity, 'remove_exit_code': removed.returncode}


def cleanup_rehearsal(proc, cidfile, name, log, receipt, run=subprocess.run):
    """Attempt each bounded cleanup independently, retaining all failures."""
    errors = []
    receipt.update(passed=False, errors=[])
    def attempt(step, action):
        try:
            receipt[step] = action()
        except BaseException as error:
            errors.append(error)
            receipt['errors'].append({'step': step, 'type': type(error).__name__, 'message': str(error)})

    attempt('container', lambda: retire_container(cidfile, name, run))
    def stop_client():
        if proc is None:
            return {'started': False}
        try:
            code = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait(timeout=5)
        return {'started': True, 'exit_code': code}
    attempt('client', stop_client)
    # A launch can create its container after our first absent read. Once the
    # client has stopped, inspect its newly written ID rather than trusting it.
    if proc is not None and not receipt.get('container', {}).get('container_id'):
        attempt('container_after_client', lambda: retire_container(cidfile, name, run))
    attempt('log_closed', lambda: (log.close(), True)[1])
    receipt['passed'] = not errors
    if errors:
        raise errors[0]


def finish_rehearsal(result, out, cleanup, verify, primary=None):
    """Persist cleanup and verification failures without masking the primary."""
    secondary = None
    if primary is not None:
        result.update(passed=False, error=str(primary),
                      primary_error={'type': type(primary).__name__, 'message': str(primary)})
    try:
        cleanup()
    except BaseException as error:
        secondary = error
        result.update(passed=False, cleanup_error={'type': type(error).__name__, 'message': str(error)})
    if result['passed']:
        try:
            verify()
        except BaseException as error:
            secondary = error
            result.update(passed=False, verification_error={'type': type(error).__name__, 'message': str(error)})
    # Fresh output directory: never replace an earlier receipt.
    with (out/'result.json').open('x') as stream:
        stream.write(json.dumps(result, indent=2)+'\n')
    print(out)
    if primary is not None:
        raise primary
    if secondary is not None:
        raise secondary
    if not result['passed']:
        raise RuntimeError(result.get('error', 'rehearsal failed'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nand', required=True, type=Path)
    p.add_argument('--qemu', required=True, type=Path)
    p.add_argument('--image', type=Path, default=ROOT/'build/os/hardware/xImage')
    p.add_argument('--key', required=True, type=Path, help='generated fixture SSH private key, never owner key')
    p.add_argument('--icount', action='store_true')
    p.add_argument('--fixture-entropy', action='store_true', help='credit fresh host random bytes over the emulator UART, not hardware entropy evidence')
    p.add_argument('--recovery-package', type=Path, help='rehearse stock recovery writes from an emulated staged SD card')
    p.add_argument('--wifi', action='store_true', help='check manual driver/host loading and unload cycles')
    p.add_argument('--transport', action='store_true', help='exercise storage and Wi-Fi stop without flashing')
    p.add_argument('--update-package', type=Path, help='upload, stage and apply using the on-device command')
    p.add_argument('--card-filesystem', choices=['vfat', 'exfat'], default='vfat')
    p.add_argument('--target-profile', choices=['diagnostic', 'installed'], default='diagnostic',
                   help='post-recovery readiness contract; initial updater boot remains diagnostic')
    p.add_argument('--initial-package', type=Path,
                   help='boot an installed source package before testing its next update; generated fixtures only')
    p.add_argument('--userdata-image', type=Path,
                   help='explicit generated UBI fixture under build/, required with --initial-package')
    p.add_argument('--require-player', action='store_true', help='check supervised player health after installed update')
    p.add_argument('--uploaded-card-image', type=Path,
                   help='copy a generated disposable card already holding incoming.tar; validation is still mandatory')
    a = p.parse_args()
    target_marker = 'Q2_INSTALLED_ROOT_READY' if a.target_profile == 'installed' else 'Q2_DIAGNOSTIC_READY'
    initial_marker = target_marker if a.recovery_package else ('Q2_INSTALLED_ROOT_READY' if a.initial_package else 'Q2_DIAGNOSTIC_READY')
    if bool(a.initial_package) != bool(a.userdata_image): p.error('installed source requires both --initial-package and --userdata-image')
    if a.initial_package and (not a.update_package or a.recovery_package): p.error('installed source requires --update-package')
    if a.userdata_image and not a.userdata_image.resolve().is_relative_to(ROOT/'build'): p.error('generated userdata must be under build/')
    if a.require_player and (a.target_profile != 'installed' or not a.update_package):
        p.error('player health requires an installed --update-package target; use test_boot.py for direct boot health')
    if a.uploaded_card_image and (not a.update_package or not a.uploaded_card_image.resolve().is_relative_to(ROOT/'build')):
        p.error('uploaded card requires update package and an image under build/')
    if a.recovery_package and a.card_filesystem == 'exfat': p.error('exFAT fixture is populated by guest upload; use --update-package')
    if a.update_package and a.recovery_package: p.error('choose one update entry path')
    data = a.image.read_bytes(); uimage(data)
    initial_blobs = initial_report = None
    if a.initial_package:
        initial_blobs, initial_report = read_package(a.initial_package)
        if initial_blobs[KERNEL] != data: raise ValueError('initial package kernel differs from initial image')
    package_blobs = package_report = None
    if a.recovery_package:
        package_blobs, package_report = read_package(a.recovery_package)
        if package_blobs[KERNEL] != data: raise ValueError('package kernel differs from tested image')
    if a.update_package:
        package_blobs, package_report = read_package(a.update_package)
    out = ROOT/'build/os/hardware'/('spl-'+uuid.uuid4().hex[:8]); out.mkdir()
    nand = out/'nand.img'
    shutil.copyfile(a.nand, nand)
    if nand.stat().st_size != 131072 * STRIDE: raise ValueError('expected complete data+OOB NAND fixture')
    # Demand erased identity/userdata. Do not capture or operate on owner state.
    with nand.open('r+b') as f:
        for start, end in [(0x6400000,0x6420000),(0x6440000,0x7d00000)]:
            f.seek(start//PAGE*STRIDE)
            if any(b != 255 for b in f.read((end-start)//PAGE*STRIDE)):
                raise ValueError('fixture must have erased identity/userdata')
        # Starting selector must be erased: cold normal boot only.
        f.seek(0x6420000//PAGE*STRIDE)
        if f.read(256) != b'\xff'*256: raise ValueError('fixture ota is not erased')
        if a.recovery_package:
            f.seek(0x6420000//PAGE*STRIDE)
            f.write(b'ota:kernel2'.ljust(256, b' '))
        else:
            f.seek(0x100000//PAGE*STRIDE)
            padded = data.ljust(6*1024**2, b'\xff')
            for i in range(0,len(padded),PAGE): f.write(padded[i:i+PAGE]+b'\xff'*64)
    # Verify every region outside main kernel against the sanitized source.
    before=nand.read_bytes(); orig=a.nand.read_bytes()
    lo,hi=0x100000//PAGE*STRIDE,0x700000//PAGE*STRIDE
    if a.recovery_package:
        lo,hi=0x6420000//PAGE*STRIDE,0x6420000//PAGE*STRIDE+256
    if before[:lo]!=orig[:lo] or before[hi:]!=orig[hi:]: raise ValueError('fixture modified protected bytes')
    if initial_blobs:
        with nand.open('r+b') as f:
            for start, limit, payload in [(0x700000,0x4b00000,initial_blobs[ROOTFS]),
                                          (0x6440000,0x18c0000,a.userdata_image.read_bytes())]:
                if len(payload)>limit: raise ValueError('initial fixture payload exceeds slot')
                f.seek(start//PAGE*STRIDE)
                for i in range(0,len(payload),PAGE): f.write(payload[i:i+PAGE].ljust(PAGE,b'\xff')+b'\xff'*64)
    if a.recovery_package or a.update_package or a.transport:
        card=out/'card'; card.mkdir()
        if a.recovery_package: stage(a.recovery_package, card, package_report['update_tar_sha256'])
        sd=out/'sd.img'
        # Incoming tar, retained tar and extracted payload coexist during stage.
        card_mib = 128
        if a.update_package:
            needed = 3 * a.update_package.stat().st_size + 16 * 1024**2
            card_mib = max(card_mib, ((needed + 128*1024**2 - 1) // (128*1024**2)) * 128)
        with sd.open('wb') as f:
            f.truncate(card_mib*1024**2)
            mbr=bytearray(512); mbr[446:462]=struct.pack('<B3sB3sII',0,b'\0'*3,7 if a.card_filesystem == 'exfat' else 12,b'\0'*3,2048,(card_mib-1)*2048)
            mbr[510:512]=b'\x55\xaa'; f.write(mbr)
        if a.uploaded_card_image:
            shutil.copyfile(a.uploaded_card_image, sd)
        elif a.card_filesystem == 'exfat':
            fs = out/'sd-partition.img'
            with fs.open('wb') as f: f.truncate((card_mib-1)*1024**2)
            subprocess.run(['mkfs.exfat', str(fs)], check=True)
            with fs.open('rb') as src, sd.open('r+b') as dst:
                dst.seek(1048576); shutil.copyfileobj(src, dst)
            fs.unlink()
        else:
            subprocess.run(['mformat','-i',str(sd)+'@@1048576','-F','-v','Q2','::'],check=True)
        if a.recovery_package: subprocess.run(['mcopy','-s','-i',str(sd)+'@@1048576',str(card/'recovery-update'),'::/'],check=True)
    qemu=out/'qemu-system-mipsel'; shutil.copyfile(a.qemu,qemu); qemu.chmod(0o755)
    port_socket=socket.socket(); port_socket.bind(('127.0.0.1',0)); port=port_socket.getsockname()[1];port_socket.close()
    prefix='/r/'+str(out.relative_to(ROOT)); cname='install-boot-'+out.name
    cidfile=out/'container.cid'
    image=subprocess.check_output(['podman','image','inspect','--format','{{.Id}}','q2-qemu-build'],text=True).strip()
    cmd=['podman','run','--rm','--name',cname,'--cidfile',str(cidfile),'--network','host','--security-opt','label=disable',
         '-v',f'{ROOT}:/r','-w','/r',image,prefix+'/qemu-system-mipsel','-M','q2,boot-source=nand',
         '-display','none','-nographic','-monitor','none','-serial','file:'+prefix+'/console.log',
         '-serial','unix:'+prefix+'/shell.sock,server=on,wait=off',
         '-drive','if=mtd,format=raw,file='+prefix+'/nand.img,snapshot=on',
         '-nic',f'user,restrict=on,net=192.168.77.0/24,host=192.168.77.1,dhcpstart=192.168.77.10,hostfwd=tcp:127.0.0.1:{port}-192.168.77.2:22',
         '-chardev','socket,id=q2ctl,path='+prefix+'/ctl.sock,server=on,wait=off']
    if a.recovery_package or a.update_package or a.transport:
        i=cmd.index('-drive')+1; cmd[i]=cmd[i].replace(',snapshot=on','')
        cmd+=['-drive','if=sd,format=raw,file='+prefix+'/sd.img']
    if a.icount: cmd+=['-icount','shift=1']
    result={'passed':False,'hardware':False,'installation_rehearsed':False,'command':cmd,
            'image_sha256':sha(a.image),'qemu_sha256':sha(qemu),'source_nand_sha256':sha(a.nand),
            'protected_fixture_bytes_unchanged':True,'icount':a.icount,'target_profile':a.target_profile}
    if initial_report:
        result['initial_package_sha256']=initial_report['update_tar_sha256']
        result['generated_userdata_sha256']=sha(a.userdata_image)
    if a.uploaded_card_image: result['uploaded_fixture_card_sha256']=sha(a.uploaded_card_image)
    log=(out/'qemu.log').open('w'); proc=None; primary=None
    try:
        proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+(600 if a.recovery_package else 180)
        while time.monotonic()<deadline:
            console=(out/'console.log').read_text(errors='replace') if (out/'console.log').exists() else ''
            if initial_marker in console: break
            if proc.poll() is not None: raise RuntimeError('QEMU exited')
            time.sleep(.2)
        else: raise TimeoutError('SPL readiness: '+initial_marker)
        if '====into normal====' not in console or (initial_marker == 'Q2_DIAGNOSTIC_READY' and 'Q2_DIAGNOSTIC_USB_READY' not in console):
            raise RuntimeError('normal SPL/USB readiness marker missing')
        result['spl_boot']=True
        if a.recovery_package:
            if '====into recovery====' not in console: raise RuntimeError('recovery SPL not observed')
            result['installation_rehearsed']='stock recovery from pre-staged SD and ota; stock menu not exercised'
            result['package_sha256']=package_report['update_tar_sha256']
        def seed_guest():
            seed=base64.b64encode(os.urandom(64)).decode()
            serial=socket.socket(socket.AF_UNIX); serial.settimeout(15); serial.connect(str(out/'shell.sock'))
            marker='Q2_FIXTURE_SEED_DONE'
            command=f'\nmkdir -p /run/seed; printf %s {seed} | base64 -d > /run/seed/seed.credit; seedrng -d /run/seed && echo {marker}\n'
            serial.sendall(command.encode()); received=b''
            while b'\r\n'+marker.encode()+b'\r\n' not in received:
                chunk=serial.recv(65536)
                if not chunk: raise RuntimeError('entropy fixture UART closed')
                received+=chunk
            serial.close()
            result['entropy']='fresh host os.urandom credited by BusyBox seedrng via emulator UART; not hardware evidence'
        if a.fixture_entropy and not (a.initial_package or (a.recovery_package and a.target_profile == 'installed')):
            seed_guest()
        ssh=['ssh','-F','/dev/null','-o','IdentityAgent=none','-i',str(a.key),'-p',str(port),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
             '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(out/'known_hosts'),
             '-o','ConnectTimeout=15','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=3','root@127.0.0.1']
        # No password fallback. The emulator must initialize entropy or this fails
        # visibly; any later fixture entropy injection must be recorded separately.
        command='set -e; uname -a; ip addr show usb0; test ! -e /dev/mtd0; test ! -e /dev/mmcblk0; echo Q2_SSH_OK'
        if a.initial_package or (a.recovery_package and a.target_profile == 'installed'):
            command='set -e; uname -a; ip addr show usb0; test "$(cat /run/q2/state-status)" = ready; test -b /dev/mtdskip0; echo Q2_SSH_OK'
        reply=subprocess.run(ssh+[command],capture_output=True,text=True,timeout=40)
        (out/'ssh.log').write_text(reply.stdout+reply.stderr)
        if reply.returncode or 'Q2_SSH_OK' not in reply.stdout: raise RuntimeError('USB SSH failed')
        result['ssh']=True
        if a.wifi:
            for cycle in range(2):
                command='set -e; test ! -d /sys/module/8733bs; test ! -e /sys/class/mmc_host/mmc1; q2-wifi start; test -d /sys/module/8733bs; test -e /sys/class/mmc_host/mmc1; sleep 2; q2-wifi stop; test ! -d /sys/module/8733bs; test ! -e /sys/class/mmc_host/mmc1; echo WIFI_MODULE_CYCLE_OK'
                checked=subprocess.run(ssh+[command],capture_output=True,text=True,timeout=60)
                (out/f'wifi-{cycle}.log').write_text(checked.stdout+checked.stderr)
                if checked.returncode or 'WIFI_MODULE_CYCLE_OK' not in checked.stdout: raise RuntimeError('Wi-Fi manual module cycle failed')
            result['wifi_module_cycles']=2
            result['wifi_association']='not tested: no chip model'

        wrong=out/'untrusted-key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','q2-untrusted-fixture','-f',str(wrong)],check=True)
        reject=ssh.copy(); reject[reject.index('-i')+1]=str(wrong)
        denied=subprocess.run(reject+['echo SHOULD_NOT_RUN'],capture_output=True,text=True,timeout=40)
        if denied.returncode==0 or 'Permission denied' not in denied.stderr:
            raise RuntimeError('untrusted SSH key was not conclusively rejected')
        result['untrusted_key_rejected']=True
        payload=bytes((i*37+i//251)&255 for i in range(131071)); expected=hashlib.sha256(payload).hexdigest()
        got=subprocess.run(ssh+['cat > /run/probe; sha256sum /run/probe'],input=payload,capture_output=True,timeout=40)
        if got.returncode or expected.encode() not in got.stdout: raise RuntimeError('USB upload digest')
        got=subprocess.run(ssh+['cat /run/probe'],capture_output=True,timeout=40)
        if got.returncode or got.stdout!=payload: raise RuntimeError('USB download digest')
        result['bidirectional_transfer_sha256']=expected
        s=socket.socket(socket.AF_UNIX); s.connect(str(out/'ctl.sock')); f=s.makefile('rw')
        for ctl in ['usb out','usb in']:
            f.write(ctl+'\n'); f.flush(); response=f.readline()
            if 'error' in response.lower(): raise RuntimeError(response)
            time.sleep(2)
        f.close();s.close()
        got=subprocess.run(ssh+['sha256sum /run/probe'],capture_output=True,timeout=40)
        if got.returncode or expected.encode() not in got.stdout: raise RuntimeError('USB reconnect')
        result['reconnect']=True
        if a.transport:
            command='set -e; q2-update-storage; echo generated-storage-fixture > /mnt/mmc/transport-check; sync; q2-wifi start; q2-wifi stop; mountpoint -q /mnt/mmc; test -b /dev/mmcblk0p1; grep -q generated-storage-fixture /mnt/mmc/transport-check; test ! -e /sys/class/mmc_host/mmc1; test -e /sys/class/mmc_host/mmc0; echo TRANSPORT_PRESERVED'
            checked=subprocess.run(ssh+[command],capture_output=True,text=True,timeout=90)
            (out/'transport.log').write_text(checked.stdout+checked.stderr)
            if checked.returncode or 'TRANSPORT_PRESERVED' not in checked.stdout: raise RuntimeError('transport isolation failed')
            result['wifi_stop_preserves_mounted_sd']=True
        if a.update_package:
            storage='mountpoint -q /mnt/mmc && test -b /dev/mmcblk0p1' if a.initial_package else 'q2-update-storage'
            checked=subprocess.run(ssh+[storage],capture_output=True,text=True,timeout=60)
            (out/'storage.log').write_text(checked.stdout+checked.stderr)
            if checked.returncode: raise RuntimeError('update storage failed')
            if not a.uploaded_card_image:
                checked=subprocess.run(ssh+['cat > /mnt/mmc/incoming.tar'],input=a.update_package.read_bytes(),capture_output=True,timeout=300)
                if checked.returncode: raise RuntimeError('upload update package failed')
            digest=package_report['update_tar_sha256']
            # stage validates the source and retained copy; do not repeat check.
            command=f'set -e; q2-hw-update stage /mnt/mmc/incoming.tar {digest} /mnt/mmc; test ! -d /mnt/mmc/recovery-update; q2-hw-update status /mnt/mmc; cat /proc/mtd'
            checked=subprocess.run(ssh+[command],capture_output=True,text=True,timeout=600)
            (out/'stage.log').write_text(checked.stdout+checked.stderr)
            if checked.returncode: raise RuntimeError('on-device staging failed')
            count=(out/'console.log').read_text(errors='replace').count(target_marker)
            try:
                checked=subprocess.run(ssh+[f'q2-hw-update apply {digest} /mnt/mmc'],capture_output=True,text=True,timeout=600)
                (out/'apply.log').write_text(checked.stdout+checked.stderr)
                if checked.returncode not in (0,255): raise RuntimeError('apply refused: '+checked.stderr.strip())
            except subprocess.TimeoutExpired as e:
                (out/'apply.log').write_bytes((e.stdout or b'')+(e.stderr or b''))
                console=(out/'console.log').read_text(errors='replace')
                if '====into recovery====' not in console: raise
                result['apply_transport']='SSH socket stayed open after reset; actual recovery entry observed'

            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                console=(out/'console.log').read_text(errors='replace')
                if console.count(target_marker)>count: break
                time.sleep(.5)
            else: raise TimeoutError('on-device apply did not reach '+target_marker)
            if '====into recovery====' not in console: raise RuntimeError('apply never booted recovery')
            result['installation_rehearsed']='on-device stage (source/copy validation)/apply -> SPL -> stock recovery -> '+a.target_profile
            result['package_sha256']=digest
            # Production installed images deliberately have no emulator UART
            # shell. Their SSH readiness must use the kernel's own entropy.
            if a.fixture_entropy and a.target_profile != 'installed': seed_guest()
            # Diagnostic roots can regenerate RAM-only host identity. An
            # installed-source update must retain its already pinned identity.
            if not a.initial_package: (out/'known_hosts').unlink(missing_ok=True)
            post_query='echo Q2_POST_UPDATE_SSH'
            if a.target_profile == 'installed':
                post_query='test "$(cat /run/q2/state-status)" = ready && test -b /dev/mtdskip0 && echo Q2_POST_UPDATE_SSH'
                if a.require_player:
                    post_query+='; n=0; while [ "$n" -lt 60 ]; do if test -s /run/q2nd/healthy && test "$(cat /run/q2nd/healthy)" = "$(cat /run/q2nd/player.pid)" && kill -0 "$(cat /run/q2nd/player.pid)"; then echo Q2_PLAYER_HEALTH_PASS; exit 0; fi; n=$((n+1)); sleep 1; done; exit 1'
            checked=subprocess.run(ssh+[post_query],capture_output=True,text=True,timeout=120 if a.require_player else 60)
            (out/'post-update.log').write_text(checked.stdout+checked.stderr)
            if checked.returncode or 'Q2_POST_UPDATE_SSH' not in checked.stdout: raise RuntimeError('post-update SSH failed')
            result['post_update_ssh']=True
            if a.require_player: result['post_update_player_health']='Q2_PLAYER_HEALTH_PASS' in checked.stdout
        result['passed']=True
    except BaseException as error:
        primary=error
    finally:
        def verify():
            if not (a.recovery_package or a.update_package): return
            actual=nand.read_bytes()
            def region(blob,start,size):
                raw=blob[start//PAGE*STRIDE:(start+size)//PAGE*STRIDE]
                return b''.join(raw[i:i+PAGE] for i in range(0,len(raw),STRIDE))
            checks={}
            for name,start,size in [('SPL',0,0x100000),('recovery',0x5200000,0x1200000),('identity',0x6400000,0x20000),('userdata',0x6440000,0x18c0000)]:
                lo=start//PAGE*STRIDE; hi=(start+size)//PAGE*STRIDE
                checks[name]=actual[lo:hi]==orig[lo:hi]
                if name=='userdata':
                    result['userdata_reset_accepted_by_user']=True
                elif not checks[name]: result['passed']=False; result['error']='protected region changed: '+name
            for name,start,size in [(KERNEL,0x100000,0x600000),(ROOTFS,0x700000,0x4b00000)]:
                checks[name]=region(actual,start,size)[:len(package_blobs[name])]==package_blobs[name]
                if not checks[name]: result['passed']=False; result['error']='flashed payload differs: '+name
            if region(actual,0x6420000,0x20000)!=b'\xff'*0x20000: result['passed']=False; result['error']='ota not cleared'
            result['partition_checks']=checks
            result['recovery_write_verified']=result['passed']
        result['cleanup']={}
        finish_rehearsal(result, out,
                         lambda: cleanup_rehearsal(proc, cidfile, cname, log, result['cleanup']),
                         verify, primary)


if __name__=='__main__':main()
