"""The RTL8733BS Bluetooth firmware files an image may carry (docs/bluetooth.md).

Realtek's Bluetooth patch and configuration for the RTL8733BS, as shipped in
Shanling's V1.32 update under /firmware/rtlbt. Not redistributable from this
repository: an image build copies the owner's extracted files and checks
them against these digests. q2nd searches /lib/firmware/rtlbt first.
Used by os/install/build.py and tests/perf/release_kernel_image.py.
"""
import hashlib
from pathlib import Path
import shutil

FILES = {
    'rtl8733bs_fw': '99603578f7f2b969e40a8156d1bdfd96f3418b4dc39770b6dc9a2eec1cb7bbe8',
    'rtl8733bs_config': 'e1bd37cbbf18362e429b05b508a3341846127c51a11565d0514b977281087297',
}
# Where the files go in the root, and the directories that holds them.
DIRECTORY = 'lib/firmware/rtlbt'
DIRECTORIES = ('lib/firmware', DIRECTORY)
TARGETS = tuple(DIRECTORY + '/' + name for name in FILES)
PATCH = 'kernel/patches/0181-MIPS-Q2-mux-UART0-for-Bluetooth-and-add-a-Bluetooth-enable-hold.patch'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def admit(directory):
    """The two files' digests, or an error naming the first unexpected one."""
    for name, expected in FILES.items():
        path = Path(directory) / name
        if not path.is_file() or path.is_symlink() or sha(path) != expected:
            raise RuntimeError('unexpected Bluetooth firmware file: ' + name)
    return dict(FILES)


def install(directory, root):
    """Install the two verified files (0644) under root; return their digests."""
    hashes = admit(directory)
    for name in FILES:
        target = Path(root) / DIRECTORY / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        shutil.copyfile(Path(directory) / name, target)
        target.chmod(0o644)
        if sha(target) != FILES[name]:
            raise RuntimeError('Bluetooth firmware changed during copying: ' + name)
    return hashes
