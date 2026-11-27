"""Ship the signed wireless database pinned by the selected Buildroot recipe."""
from pathlib import Path
import hashlib
import tarfile

from build import fetch

VERSION = '2026.09.03'
SOURCE = {
    'url': 'https://cdn.kernel.org/pub/software/network/wireless-regdb/'
           f'wireless-regdb-{VERSION}.tar.xz',
    'sha256': 'b22e0901227b820cd1c280abe681a15b773a5103a5e10dc442e94ebb34cbf58d',
}
LICENSE_SHA256 = '678b0df753c86198fc496d1f1033429bbd57f101472132ee7eaaf9f5e0a7fae1'
FILES = {
    'regulatory.db': 'lib/firmware/regulatory.db',
    'regulatory.db.p7s': 'lib/firmware/regulatory.db.p7s',
    'LICENSE': 'usr/share/licenses/wireless-regdb/LICENSE',
}


def read():
    archive = fetch(SOURCE)
    payload = {}
    with tarfile.open(archive) as source:
        for name, destination in FILES.items():
            member = source.getmember(f'wireless-regdb-{VERSION}/{name}')
            if not member.isfile() or not 0 < member.size <= 256 * 1024:
                raise ValueError('invalid wireless-regdb archive member: ' + name)
            with source.extractfile(member) as stream:
                payload[destination] = stream.read()
    if hashlib.sha256(payload[FILES['LICENSE']]).hexdigest() != LICENSE_SHA256:
        raise ValueError('wireless-regdb license differs from Buildroot pin')
    return payload


def record(payload):
    return {'source': SOURCE, 'license': 'ISC',
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}


def install(root, payload):
    for name, data in payload.items():
        target = root / Path(name)
        if target.resolve() != target.absolute():
            raise ValueError('wireless-regdb requires a canonical destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
