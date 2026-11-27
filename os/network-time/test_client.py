#!/usr/bin/env python3
"""Exercise an explicitly supplied native chrony build against synthetic NTP.

chronyd -x never changes the host clock. Does not contact a public server.
"""
import argparse
import json
import os
import pwd
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[2]


def timestamp(now):
    value = now + 2208988800
    return struct.pack('!II', int(value), int((value % 1) * 2**32))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-bin-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'build/network-time-client')
    a = p.parse_args()
    out = a.output.resolve()
    if not out.is_relative_to(ROOT / 'build'):
        p.error('output must be under build/')
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(('127.0.0.1', 0))
    server.settimeout(0.5)
    stop = threading.Event()
    requests = []

    def respond():
        while not stop.is_set():
            try:
                request, address = server.recvfrom(4096)
            except TimeoutError:
                continue
            if len(request) < 48:
                continue
            requests.append(time.monotonic())
            # Synthetic stratum-2 UTC server: echo the client's transmit token,
            # and use local current time without setting any system clock.
            reply = bytearray(48)
            reply[0:4] = bytes([0x24, 2, 6, 236])
            reply[4:12] = struct.pack('!II', 66, 66)
            reply[12:16] = b'TEST'
            reply[16:24] = timestamp(time.time() - 1)
            reply[24:32] = request[40:48]
            reply[32:40] = timestamp(time.time())
            reply[40:48] = timestamp(time.time())
            server.sendto(reply, address)

    thread = threading.Thread(target=respond)
    thread.start()
    control = out / 'chronyd.sock'
    control.unlink(missing_ok=True)
    config = out / 'chrony.conf'
    config.write_text(f'''server 127.0.0.1 port {server.getsockname()[1]} iburst minpoll 0 maxpoll 0
pidfile {out / 'chronyd.pid'}
bindcmdaddress {control}
cmdport 0
port 0
''')
    process = None
    try:
        with (out / 'chronyd.log').open('w') as log:
            process = subprocess.Popen([str(a.native_bin_dir / 'chronyd'), '-U', '-x', '-d',
                '-u', pwd.getpwuid(os.getuid()).pw_name, '-f', str(config)], stdout=log, stderr=log)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('chronyd exited; see generated log')
                if control.exists():
                    result = subprocess.run([str(a.native_bin_dir / 'chronyc'), '-h', str(control), 'waitsync', '1'], capture_output=True, text=True, timeout=3)
                    if result.returncode == 0:
                        break
                time.sleep(0.25)
            else:
                raise RuntimeError('synthetic NTP did not synchronize')
            tracking = subprocess.check_output([str(a.native_bin_dir / 'chronyc'), '-h', str(control), 'tracking'], text=True)
            assert 'Normal' in tracking
            (out / 'tracking.txt').write_text(tracking)
            (out / 'result.json').write_text(json.dumps({'result': 'pass', 'synthetic_ntp_replies': len(requests),
                'clock_control': 'disabled with chronyd -x', 'hardware_qualified': False}, indent=2) + '\n')
            print(f'PASS: {len(requests)} generated NTP responses; host clock changes disabled')
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        stop.set()
        thread.join(timeout=2)
        server.close()


if __name__ == '__main__':
    main()
