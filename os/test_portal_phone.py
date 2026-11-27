#!/usr/bin/env python3
"""Exercise an already built q2-portal using two isolated network namespaces.

Virtual Ethernet carries the real HTTP traffic. Stand-ins for iw and the AP
daemons observe radio requests without opening a physical interface. The
actual AP shell watcher owns and reaps those processes and the virtual address.
"""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'os/install/integration/usr/libexec/q2-network/portal-phone-ap'
RUN = Path('/run/q2-portal-phone')
NETWORK = Path('/usr/libexec/q2-network')
ADDR = '192.168.241.1'
ACCOUNT = 'generated-traveler'
PASSWORD = 'synthetic-phone-passphrase'


def run(*argv, timeout=10, **kwargs):
    return subprocess.run(argv, check=True, timeout=timeout, **kwargs)


def until(predicate, seconds=7):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('fixture condition timed out')
        time.sleep(0.05)


def exec_script(path, text):
    path.write_text(text)
    path.chmod(0o755)


def stage(directory):
    sysfs = directory / 'sys'
    phy = sysfs / 'phy0'
    phy.mkdir(parents=True)
    for name in ('wlan0', 'wlan1'):
        device = sysfs / 'class/net' / name
        device.mkdir(parents=True)
        (device / 'phy80211').symlink_to(phy)
        (device / 'flags').write_text('0x0\n')
    (directory / 'station').write_text('none\n')
    mock = directory / 'libexec/q2-network'
    mock.mkdir(parents=True)

    text = SCRIPT.read_text()
    text = text.replace('SYS=/sys\n', f'SYS={sysfs}\n')
    assert f'SYS={sysfs}\n' in text
    exec_script(mock / SCRIPT.name, text)

    exec_script(mock / 'iw', f"""#!/bin/sh
set -eu
case "$*" in
'phy phy0 info')
cat <<'END'
Wiphy phy0
    Band 1:
        Frequencies:
            * 2412.0 MHz [1] (20.0 dBm)
            * 2437.0 MHz [6] (20.0 dBm)
            * 2462.0 MHz [11] (disabled)
    Band 2:
        Frequencies:
            * 5180.0 MHz [36] (20.0 dBm)
            * 5200.0 MHz [40] (no IR)
            * 5260.0 MHz [52] (radar detection)
            * 5280.0 MHz [56] (no 20MHz)
            * 5955.0 MHz [1] (20.0 dBm)
END
;;
'dev wlan0 link')
    frequency=$(cat '{directory}/station')
    if [ "$frequency" = none ]; then
        printf 'Not connected.\\n'
    else
        printf 'Connected to 02:00:00:00:00:01 (on wlan0)\\n\\tfreq: %s\\n' "$frequency"
    fi
    ;;
'dev wlan1 info') printf '\\ttype managed\\n' ;;
'dev wlan1 link') printf 'Not connected.\\n' ;;
'dev wlan1 set type __ap'|'dev wlan1 set type managed')
    printf '%s\\n' "$*" >> '{directory}/radio-events' ;;
*) exit 1 ;;
esac
""")
    exec_script(mock / 'hostapd_cli', "#!/bin/sh\nprintf 'state=ENABLED\\n'\n")
    stand_in = directory / 'ap-stand-in.c'
    stand_in.write_text('''
#define _POSIX_C_SOURCE 200809L
#include <unistd.h>
int main(void) {
    for (;;) pause();
}
''')
    run('cc', '-std=c11', '-Wall', '-Wextra', '-Werror', stand_in, '-o', mock / 'hostapd')
    shutil.copy2(mock / 'hostapd', mock / 'dnsmasq')
    run('mount', '-t', 'tmpfs', 'tmpfs', '/run')
    run('mount', '--bind', directory / 'libexec', '/usr/libexec')


def phone_namespace(directory):
    # The isolated phone process has no commands other than an interruptible
    # wait. Requests are executed inside its namespace via nsenter.
    ready = directory / 'phone-ready'
    phone = subprocess.Popen(['unshare', '-n', 'sh', '-ec', 'touch "$1"; exec sleep 120',
                              'phone-peer', ready])
    try:
        until(ready.is_file)
        run('ip', 'link', 'add', 'wlan1', 'type', 'veth', 'peer', 'name', 'phone0')
        run('ip', 'link', 'set', 'phone0', 'netns', str(phone.pid))
        run('nsenter', '-t', str(phone.pid), '-n', 'ip', 'addr', 'add',
            '192.168.241.2/24', 'dev', 'phone0')
        run('nsenter', '-t', str(phone.pid), '-n', 'ip', 'link', 'set', 'phone0', 'up')
    except BaseException:
        phone.kill()
        phone.wait()
        raise
    return phone


def helper(binary):
    process = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    process.stdin.write('{"v":1,"op":"pair"}\n')
    process.stdin.close()
    return process


def event(process, state):
    assert select.select([process.stdout], [], [], 12)[0], 'helper event timed out'
    line = process.stdout.readline()
    assert line, 'helper exited before producing an event'
    received = json.loads(line)
    assert received['state'] == state, f"expected {state}, got {received['state']}"
    return received


def qr(process):
    row = event(process, 'qr')
    wifi = row['ap']
    url = urllib.parse.urlsplit(row['page'])
    assert wifi.startswith('WIFI:T:WPA;S:Q2-SignIn-') and ';P:q2-' in wifi and wifi.endswith(';;')
    assert url.scheme == 'http' and url.hostname == ADDR and url.path == '/' and len(url.fragment) == 64
    assert url.fragment not in wifi
    return url._replace(fragment='').geturl(), url.fragment


def phone_request(url, expected, token=None):
    if token is None:
        request = urllib.request.Request(url)
    else:
        body = json.dumps({'username': ACCOUNT, 'password': PASSWORD}).encode()
        request = urllib.request.Request(url + 'entry', data=body, headers={
            'Authorization': 'Bearer ' + token,
            'Origin': 'http://' + ADDR,
            'Content-Type': 'application/json',
        })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.code == expected, (response.code, expected)
        text = response.read().decode()
        assert ACCOUNT not in text and PASSWORD not in text
        if token is None:
            assert 'Q2 Wi-Fi sign-in' in text and 'Send to my Q2' in text


def peer_request(peer, url, status, token=None):
    # Pass only generated fixture data between namespaces.
    run('nsenter', '-t', str(peer.pid), '-n', sys.executable, __file__, '--request',
        input=json.dumps([url, status, token]), text=True)


def owned_pids():
    watcher = int((RUN / 'ready').read_text().split()[0])
    return [watcher] + [int((RUN / (name + '.pid')).read_text()) for name in ('hostapd', 'dnsmasq')]


def living(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def check_cleanup(directory, pids, before):
    until(lambda: not RUN.exists() and not any(map(living, pids)))
    local = run('ip', '-4', '-o', 'addr', 'show', 'dev', 'wlan1', capture_output=True, text=True).stdout
    assert not local.strip()
    assert (directory / 'radio-events').read_text().splitlines()[before:] == [
        'dev wlan1 set type __ap', 'dev wlan1 set type managed',
    ]


def process_owner(binary, directory, peer, scenario):
    before = len((directory / 'radio-events').read_text().splitlines()) if (
        directory / 'radio-events').exists() else 0
    (directory / 'station').write_text('none\n' if scenario == 'submit' else '5180\n')
    process = helper(binary)
    try:
        url, token = qr(process)
        pids = owned_pids()
        hostapd = (RUN / 'hostapd.conf').read_text()
        dnsmasq = (RUN / 'dnsmasq.conf').read_text()
        assert 'interface=wlan1\n' in hostapd and 'rsn_pairwise=CCMP\n' in hostapd
        assert ('channel=1\n' if scenario == 'submit' else 'channel=36\n') in hostapd
        assert (RUN / 'hostapd.conf').stat().st_mode & 0o777 == 0o600
        assert all(line in dnsmasq for line in ('interface=wlan1\n', 'port=0\n',
                                               'dhcp-option=3\n', 'dhcp-option=6\n'))
        if scenario == 'submit':
            # Packets to the same address from the device stack arrive through
            # lo. Only the paired virtual Ethernet ingress can reach this page.
            with socket.socket() as loopback:
                loopback.settimeout(1)
                assert loopback.connect_ex((ADDR, 80)) != 0
            peer_request(peer, url, 200)
            peer_request(peer, url, 403, '0' * 64 if token != '0' * 64 else '1' * 64)
            peer_request(peer, url, 200, token)
            result = event(process, 'prepared')
            assert result['user'] == ACCOUNT and result['password'] == PASSWORD
        elif scenario == 'cancel':
            # Backup cleanup applies when Back kills the credential helper.
            process.send_signal(signal.SIGKILL)
        else:
            # The helper must end on a conflicting association without a
            # station-mode change of its own.
            (directory / 'station').write_text('2437\n')
            event(process, 'failed')
        process.wait(timeout=8)
        check_cleanup(directory, pids, before)
        assert not process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--outer-net')
    parser.add_argument('--request', action='store_true')
    args = parser.parse_args()
    if args.request:
        url, status, token = json.load(sys.stdin)
        phone_request(url, status, token)
        return
    if not args.binary or not args.binary.is_file():
        parser.error('--binary must name an already built host q2-portal')
    binary = args.binary.resolve()
    namespace = os.readlink('/proc/self/ns/net')
    if not args.outer_net:
        run('unshare', '-Urnmpf', '--mount-proc', '--kill-child', sys.executable, __file__, '--binary', binary,
            '--outer-net', namespace, timeout=45)
        return
    uid_map = Path('/proc/self/uid_map').read_text().split()
    assert (args.outer_net != namespace and os.geteuid() == 0 and os.getpid() == 1
            and len(uid_map) == 3 and uid_map[0] == '0' and uid_map[2] == '1'), (
        'isolated user, network and process namespaces required')
    ranges = run('ip', '-o', 'link', 'show', capture_output=True, text=True).stdout.splitlines()
    assert len(ranges) == 1 and ': lo:' in ranges[0], 'fixture starts with loopback only'
    ip_forward = Path('/proc/sys/net/ipv4/ip_forward')
    if ip_forward.read_text().strip() != '0':
        ip_forward.write_text('0\n')
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='portal-phone-fixture-', dir=ROOT / 'build') as temporary:
        directory = Path(temporary)
        stage(directory)
        try:
            expected = run(NETWORK / SCRIPT.name, 'frequencies', capture_output=True, text=True).stdout
            assert expected.splitlines() == ['2412', '2437', '5180']
            peer = phone_namespace(directory)
            try:
                for scenario in ('submit', 'cancel', 'station-change'):
                    process_owner(binary, directory, peer, scenario)
                    print(scenario + ': passed', flush=True)
            finally:
                peer.kill()
                peer.wait()
        finally:
            run('umount', '/usr/libexec')
            run('umount', '/run')


if __name__ == '__main__':
    main()
