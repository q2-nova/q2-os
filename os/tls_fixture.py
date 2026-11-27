"""Generate ephemeral test-owned certificates; never read host identity files."""
import base64
import subprocess
import time
from pathlib import Path


def check(guest):
    dest = guest.out / 'tls'
    dest.mkdir()
    def openssl(*args):
        subprocess.run(['openssl', *args], cwd=dest, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    openssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
            '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem', '-days', '2',
            '-subj', '/CN=Q2 synthetic test CA', '-addext', 'basicConstraints=critical,CA:TRUE')
    openssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
            '-nodes', '-keyout', 'server.key', '-out', 'server.csr', '-subj', '/CN=Q2 synthetic test server')
    (dest / 'extensions').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n')
    openssl('x509', '-req', '-in', 'server.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
            '-set_serial', '2', '-days', '1', '-extfile', 'extensions', '-out', 'server.pem')
    for name in ['ca', 'server']:
        openssl('x509', '-in', name + '.pem', '-outform', 'DER', '-out', name + '.der')
    openssl('pkcs8', '-topk8', '-nocrypt', '-in', 'server.key', '-outform', 'DER', '-out', 'key.der')
    guest.check('controlled TLS clock', f'date -u -s @{int(time.time())} && mkdir -p /tmp/q2-tls')
    for name in ['ca.der', 'server.der', 'key.der']:
        data = base64.b64encode((dest / name).read_bytes()).decode()
        guest.check('install ephemeral TLS fixture ' + name, f"printf '%s' '{data}' | base64 -d > /tmp/q2-tls/{name}")
    guest.check('TLS certificate validation and local sockets', 'q2-rust-probe /tmp/q2-tls', 90)
