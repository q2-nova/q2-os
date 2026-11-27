#!/usr/bin/env python3
"""Build fresh synthetic UBI state with pre-first-player telemetry classification.

No existing userdata, settings or media are accepted as inputs. This is only a
mutable generated emulator fixture; candidate packages remain byte-for-byte.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'os/install'))
import startup_read_ahead as reader
import telemetry_fixture


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def fresh(path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/'build') or path.exists():raise ValueError('fresh generated build directory required')
    path.mkdir(parents=True,mode=0o700)
    return path


def tools(out):
    out=fresh(out);recipe=ROOT/'os/install/TelemetryFixture.Containerfile'
    with (out/'build.log').open('xb') as log:
        subprocess.run(['podman','build','--network=host','--iidfile',str(out/'image-id'),'-f',str(recipe),str(recipe.parent)],
                       cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
    image=(out/'image-id').read_text().strip()
    if not re.fullmatch('sha256:[0-9a-f]{64}',image):raise ValueError('immutable tool image identity required')
    probe=subprocess.run(['podman','run','--rm','--network','none','--security-opt','label=disable',image,'sh','-c',
        'dpkg-query -W -f=\'${Version}\\n\' mtd-utils; sha256sum /usr/sbin/mkfs.ubifs /usr/sbin/ubinize'],capture_output=True,check=True,timeout=20)
    (out/'tools.log').write_bytes(probe.stdout)
    result=dict(schema=1,passed=True,image=image,recipe_sha256=sha(recipe),tools_sha256=sha(out/'tools.log'))
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');return result


def stage(out,run_id,*,endpoint=None,token=None,tailnet=None):
    """Construct synthetic state with the generated telemetry overlay."""
    out=fresh(out);tree=out/'root';state=tree/'q2-mainline';context=state/'.telemetry-fixture'
    context.mkdir(parents=True,mode=0o700);data=state/'Q2Navidrome';data.mkdir(mode=0o700)
    config=b'[wifi]\nenabled=false\n[tailscale]\nenabled=false\n[power]\nsleep_minutes=0\n[display]\nclicker=false\n'
    if tailnet is not None:
        config=config.replace(b'[tailscale]\nenabled=false',b'[tailscale]\nenabled=true')
        if tailnet.keys is not None:
            keys=state/'network'/'tailscale-rs';keys.mkdir(parents=True,mode=0o700)
            (keys/'keys.json').write_bytes(tailnet.keys);(keys/'keys.json').chmod(0o600)
    # Installed players with no generated card read the NAND settings snapshot.
    # Retain the original path for fixtures built against older app layouts.
    for name in ('config.toml', 'config.last-valid.toml'):
        path=data/name;path.write_bytes(config);path.chmod(0o600)
    overlay=telemetry_fixture.render(run_id,endpoint=endpoint,token=token)
    (context/'overlay.toml').write_bytes(overlay);(context/'overlay.toml').chmod(0o600)
    (context/'owner').write_text(f'Q2_TELEMETRY_FIXTURE 1 {run_id} {sha(context/"overlay.toml")}\n');(context/'owner').chmod(0o600)
    return out,dict(schema=1,generated_only=True,run_id=run_id,telemetry=telemetry_fixture.projection(run_id,endpoint=endpoint,token=token),
                    tree_sha256={str(p.relative_to(tree)):sha(p) for p in tree.rglob('*') if p.is_file()})


def build(out,tool_receipt,run_id,*,endpoint=None,token=None,tailnet=None):
    if endpoint is None or token is None:raise ValueError('generated receiver base endpoint and synthetic token required')
    tool_receipt=Path(tool_receipt).resolve();tool=json.loads(tool_receipt.read_text())
    if (not tool_receipt.is_relative_to(ROOT/'build') or tool.get('passed') is not True
        or not re.fullmatch('sha256:[0-9a-f]{64}',tool.get('image',''))
        or tool['recipe_sha256']!=sha(ROOT/'os/install/TelemetryFixture.Containerfile')
        or tool['tools_sha256']!=sha(tool_receipt.parent/'tools.log')):
        raise ValueError('exact generated UBI tools receipt required')
    out,result=stage(out,run_id,endpoint=endpoint,token=token,tailnet=tailnet)
    (out/'ubinize.cfg').write_text('[user_data]\nmode=ubi\nimage=/fixture/userdata.ubifs\nvol_id=0\nvol_type=dynamic\nvol_name=user_data\nvol_size=16MiB\nvol_flags=autoresize\n')
    name='q2-generated-userdata-'+uuid.uuid4().hex[:12]
    command=['podman','run','--rm','--name',name,'--network','none','--security-opt','label=disable','-v',f'{out}:/fixture',tool['image'],'sh','-ec',
        'mkfs.ubifs -U -r /fixture/root -m 2048 -e 126976 -c 198 -o /fixture/userdata.ubifs; '
        'ubinize -o /fixture/userdata.ubi -m 2048 -p 131072 -s 2048 /fixture/ubinize.cfg']
    try:
        with (out/'build.log').open('xb') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
    finally:
        subprocess.run(['podman','rm','--force','--ignore',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=True)
        check=subprocess.run(['podman','container','exists',name],capture_output=True,timeout=10)
        (out/'cleanup.json').write_text(json.dumps(dict(container=name,absent=check.returncode==1))+'\n')
        if check.returncode!=1:raise RuntimeError('owned userdata builder container remains')
    result.update(passed=True,tool_receipt_path=str(tool_receipt),tool_receipt_sha256=sha(tool_receipt),tool_image=tool['image'],userdata_sha256=sha(out/'userdata.ubi'),
                  producer_sha256=sha(__file__),cleanup_sha256=sha(out/'cleanup.json'),hook_sha256=sha(ROOT/'os/install/root-init'),fixture_only=True)
    (out/'userdata.telemetry.json').write_text(json.dumps(result,indent=2)+'\n');return result


def admit(image,candidate, *, require_candidate_hook=True):
    image=Path(image);receipt=image.with_suffix('.telemetry.json')
    value=json.loads(receipt.read_text())
    if (value.get('passed') is not True or value.get('generated_only') is not True
        or value.get('fixture_only') is not True or value.get('userdata_sha256')!=sha(image)
        or value.get('producer_sha256')!=sha(__file__) or value.get('hook_sha256')!=sha(ROOT/'os/install/root-init')):
        raise ValueError('generated telemetry userdata identity changed')
    if require_candidate_hook:
        candidate = Path(candidate)
        manifest = candidate/'artifact.json'
        record = json.loads(manifest.read_bytes()).get('startup_read_ahead') if manifest.is_file() else None
        if record is not None:
            record = reader.checked_record(record, candidate/'root'/reader.APP)
        template = (ROOT/'os/install/root-init').read_bytes().decode()
        expected = reader.render(template, record).encode()
        if (candidate/'root'/reader.INIT).read_bytes() != expected:
            raise ValueError('candidate lacks exact pre-first-player fixture hook')
    telemetry_fixture.settings(value['run_id'])
    return dict(path=str(receipt.resolve()),sha256=sha(receipt),run_id=value['run_id'],
                userdata_sha256=value['userdata_sha256'],preboot_provisioned=True,candidate_hook_verified=require_candidate_hook,effective_context_verified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['tools','build']);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--tools',type=Path);p.add_argument('--run-id')
    p.add_argument('--endpoint');p.add_argument('--token-file',type=Path,help='explicit generated receiver token under build/')
    a=p.parse_args()
    if a.action=='tools':tools(a.out)
    else:
        if not a.tools or not a.run_id or not a.endpoint or not a.token_file:p.error('build requires --tools, --run-id, --endpoint and --token-file')
        token_path=a.token_file.resolve()
        if not token_path.is_relative_to(ROOT/'build') or token_path.is_symlink() or token_path.stat().st_size>65:p.error('bounded generated token under build/ required')
        build(a.out,a.tools,a.run_id,endpoint=a.endpoint,token=token_path.read_text().strip())

if __name__=='__main__':main()
