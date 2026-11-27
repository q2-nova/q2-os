"""SSH to the stock-firmware Q2 player and wait for its launcher's readiness.

`keys` is a directory holding the client key (`client_ed25519`) and the
player's pinned host key (`known_hosts`, alias `q2-player`), as
q2/ssh/provision.py writes them to build/ssh/device. The player's Dropbear
listens on port 2222.
"""
import subprocess
import time

CHECK = '''
if test -f /run/q2nd/restart && test -f /run/q2nd/launcher.wake; then
 kill -USR1 "$(cat /run/q2nd/launcher.pid)" 2>/dev/null || :
fi
test ! -f /run/q2nd/restart &&
test -s /run/q2nd/ready &&
test "$(cat /run/q2nd/ready)" = "$(cat /run/q2nd/player.pid)" &&
kill -0 "$(cat /run/q2nd/player.pid)" &&
echo PLAYER_READY &&
cat /proc/asound/card0/pcm0p/sub0/status &&
sha256sum /mnt/mmc/.q2nd/player
'''


def ssh(host, keys):
    """The ssh argv prefix for one command on the player."""
    return ['ssh', '-F', '/dev/null', '-i', str(keys / 'client_ed25519'), '-p', '2222', '-o', 'IdentitiesOnly=yes',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=q2-player',
            '-o', f'UserKnownHostsFile={keys}/known_hosts', '-o', 'ConnectTimeout=8', 'root@' + host]


def wait_ready(conn, timeout=70):
    """Wait until the launcher reports a ready player with a closed PCM.

    A single USR1 can race the launcher's entry into `wait`, so the check
    resends it while a restart is pending."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = subprocess.run(conn + [CHECK], capture_output=True, text=True, timeout=20)
            if result.returncode == 0 and result.stdout.startswith('PLAYER_READY\nclosed\n'):
                return result.stdout
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    raise RuntimeError('Normal player did not confirm readiness with closed PCM after the trial')
