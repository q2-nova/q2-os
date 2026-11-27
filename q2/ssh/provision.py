#!/usr/bin/env python3
"""Generate device-specific SSH keys in ignored build/ssh/device (no secrets printed)."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'build/ssh/device'
DEST.mkdir(parents=True,exist_ok=True)
DEST.chmod(0o700)
client = DEST / 'client_ed25519'
if not client.exists():
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','q2-player-development','-f',str(client)],check=True)
qemu = [str(ROOT/'build/qemu/qemu-mipsel-static'),'-cpu','P5600','-L',str(ROOT/'build/firmware-dl/root'),str(ROOT/'build/ssh/bin/dropbearkey')]
host = DEST / 'hostkey'
if not host.exists():
    subprocess.run(qemu+['-t','ed25519','-f',str(host)],check=True,stdout=subprocess.DEVNULL)
public = subprocess.check_output(qemu+['-y','-f',str(host)],text=True)
public = next(line for line in public.splitlines() if line.startswith('ssh-ed25519 '))
(DEST/'known_hosts').write_text('q2-player '+public+'\n')
(DEST/'authorized_keys').write_bytes(Path(str(client)+'.pub').read_bytes())
(DEST/'enabled').touch()
for path in DEST.iterdir(): path.chmod(0o600)
print('Device keys ready in build/ssh/device. Only authorized_keys, hostkey and enabled go on the card.')
