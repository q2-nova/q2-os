#!/usr/bin/env python3
"""Read back both stock roots and prove signed-regdb acceptance in direct QEMU."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

FIRMWARE = ('lib/firmware/regulatory.db', 'lib/firmware/regulatory.db.p7s',
            'usr/share/licenses/wireless-regdb/LICENSE')
PAGE, STRIDE = 2048, 2112
ROOT = Path(__file__).resolve().parents[2]


def output(command):
    return subprocess.check_output([str(word) for word in command])


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def verify_roots(image, record):
    expected = record['regulatory_database']['files']
    if set(expected) != set(FIRMWARE):
        raise ValueError('stock image lacks a complete regulatory database receipt')
    root = ['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
            '-v', f'{image}:/image:ro', 'q2-os-build']
    contents = {}
    for filename in FIRMWARE:
        from_disk = output([*root, 'unsquashfs', '-cat', '/image/rootfs.squashfs', filename])
        from_rescue = output(['cpio', '--quiet', '-i', '--to-stdout', '-F',
                             image / 'rescue.cpio', filename])
        if from_disk != from_rescue or checksum(from_disk) != expected[filename]:
            raise ValueError('regulatory input differs between packed roots: ' + filename)
        contents[filename] = from_disk
    return contents


def write_nand(path, image):
    with path.open('xb') as nand:
        block = b'\xff' * (1024 * STRIDE)
        for _ in range(128):
            nand.write(block)
        for name, offset in (('xImage', 0x100000), ('rootfs.squashfs', 0x700000)):
            nand.seek(offset // PAGE * STRIDE)
            data = (image / name).read_bytes()
            for start in range(0, len(data), PAGE):
                nand.write(data[start:start + PAGE].ljust(STRIDE, b'\xff'))


def rsp_packet(sock, stream):
    while (marker := stream.read(1)) != b'$':
        if not marker:
            raise EOFError('QEMU debugger socket closed')
    body = bytearray()
    while (piece := stream.read(1)) != b'#':
        if not piece:
            raise EOFError('incomplete QEMU debugger packet')
        body += piece
    received = int(stream.read(2), 16)
    if received != sum(body) & 255:
        raise ValueError('QEMU debugger packet checksum differs')
    sock.sendall(b'+')
    return bytes(body)


def rsp_command(sock, stream, command):
    sock.sendall(b'$' + command + f'#{sum(command) & 255:02x}'.encode())
    return rsp_packet(sock, stream)


def read_memory(sock, stream, address, length):
    answer = rsp_command(sock, stream, f'm{address:x},{length:x}'.encode())
    if answer.startswith(b'E') or len(answer) != 2 * length:
        raise ValueError('QEMU did not return the requested kernel memory')
    return bytes.fromhex(answer.decode())


def kernel_database(socket_path, symbol, expected):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10)
        sock.connect(str(socket_path))
        with sock.makefile('rb', buffering=0) as stream:
            # Opening QEMU's GDB connection pauses the VM. Query the
            # explicit stop status before reading from its CPU memory.
            if not rsp_command(sock, stream, b'?').startswith((b'T', b'S')):
                raise ValueError('QEMU failed to stop for the read-only kernel probe')
            pointer = int.from_bytes(read_memory(sock, stream, symbol, 4), 'little')
            if pointer < 0x80000000 or pointer >= 0xfffff000:
                raise ValueError('regulatory callback did not retain an accepted database')
            actual = b''.join(read_memory(sock, stream, pointer + index, min(512, len(expected) - index))
                              for index in range(0, len(expected), 512))
            if actual != expected:
                raise ValueError('running kernel retained a different database')
            return checksum(actual)


def boot(out, image, qemu, symbol, expected):
    write_nand(out / 'nand.img', image)
    serial = out / 'serial.log'
    cid = out / 'qemu.cid'
    with tempfile.TemporaryDirectory(prefix='q2-regdb-') as debug:
        command = ['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
                   '--cidfile', cid, '-v', f'{out}:/fixture', '-v', f'{image}:/image:ro',
                   '-v', f'{qemu}:/q2/system:ro', '-v', f'{debug}:/debug',
                   'q2-qemu-build', '/q2/system',
                   '-M', 'q2', '-display', 'none', '-monitor', 'none', '-no-reboot',
                   '-nic', 'none', '-serial', 'stdio', '-gdb', 'unix:/debug/gdb.sock,server=on,wait=off',
                   '-kernel', '/image/xImage', '-drive', 'if=mtd,format=raw,file=/fixture/nand.img']
        with serial.open('xb') as log:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 120
                while b'Q2_INSTALLED_ROOT_READY' not in serial.read_bytes().splitlines():
                    if proc.poll() is not None or time.monotonic() >= deadline:
                        raise ValueError('QEMU did not mount the stock root')
                    time.sleep(0.5)
                digest = kernel_database(Path(debug) / 'gdb.sock', symbol, expected)
                archive = serial.read_bytes()
                if b'failed to load regulatory.db' in archive or b'signature is missing/invalid' in archive:
                    raise ValueError('QEMU logged a regulatory database admission failure')
                return digest
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                if cid.is_file():
                    subprocess.run(['podman', 'rm', '--force', '--ignore', cid.read_text().strip()],
                                   check=True, stdout=subprocess.DEVNULL, timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'kit', 'qemu', 'out'):
        parser.add_argument('--' + name, required=True, type=lambda path: Path(path).resolve())
    args = parser.parse_args()
    image = args.image
    record = json.loads((image / 'artifact.json').read_text())
    if record['qemu_fixture'] is not False or record['diagnostic_ssh'] != {'profile': 'disabled'}:
        raise ValueError('regulatory readback requires a keyless stock image')
    if not args.out.is_relative_to(ROOT / 'build'):
        raise ValueError('regulatory readback output must be under build/')
    for name in ('xImage', 'rootfs.squashfs', 'rescue.cpio'):
        if checksum((image / name).read_bytes()) != record['artifacts'][name]['sha256']:
            raise ValueError('image artifact differs from its manifest: ' + name)
    for name in ('xImage', 'rescue.cpio'):
        if (image / name).read_bytes() != (args.kit / 'kernel' / name).read_bytes():
            raise ValueError('image differs from the stock kernel kit: ' + name)
    kernel_config = (args.kit / 'kernel/linux.config').read_bytes()
    if checksum(kernel_config) != record['kernel_config_sha256']:
        raise ValueError('kernel configuration differs from its image manifest')
    config = kernel_config.decode().splitlines()
    if 'CONFIG_CFG80211_REQUIRE_SIGNED_REGDB=y' not in config:
        raise ValueError('kernel does not enforce regulatory signatures')
    symbols = output(['nm', args.kit / 'kernel/kernel/vmlinux']).decode().splitlines()
    matches = [int(line.split()[0], 16) for line in symbols if line.split()[-1] == 'regdb']
    if len(matches) != 1:
        raise ValueError('stock kernel must expose one regulatory database symbol in vmlinux')
    content = verify_roots(image, record)
    args.out.mkdir(parents=True)
    digest = boot(args.out, image, args.qemu, matches[0], content[FIRMWARE[0]])
    result = {'signed_regdb_loaded': True, 'loaded_database_sha256': digest,
              'packed_roots': record['regulatory_database']['files'],
              'os_source_commit': record['os_source_commit'],
              'kernel_source_commit': record['kernel_source_commit'],
              'xImage_sha256': record['artifacts']['xImage']['sha256'],
              'qemu_sha256': checksum(args.qemu.read_bytes())}
    with (args.out / 'result.json').open('x') as report:
        json.dump(result, report, indent=2)
        report.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
