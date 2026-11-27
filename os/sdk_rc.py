#!/usr/bin/env python3
"""Finish a signed OS RC using its original SDK qualification artifacts."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SDK_ARTIFACTS = {'q2-sdk-qualification', 'q2-os-platform'}


def gh(*args):
    return subprocess.check_output(['gh', *map(str, args)], text=True)


def api(repository, endpoint, *args):
    return json.loads(gh('api', f'repos/{repository}/{endpoint}', *args))


def tag_commit(repository, tag):
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+-rc\.[1-9][0-9]*', tag):
        raise ValueError('an exact versioned RC tag is required')
    ref = api(repository, f'git/ref/tags/{tag}')
    if ref['ref'] != f'refs/tags/{tag}' or ref['object']['type'] != 'tag':
        raise ValueError('a signed annotated release tag is required')
    annotation = api(repository, f"git/tags/{ref['object']['sha']}")
    commit = annotation['object']['sha']
    if (annotation['tag'] != tag or annotation['object']['type'] != 'commit'
            or not annotation['verification']['verified']
            or not re.fullmatch(r'[0-9a-f]{40}', commit)):
        raise ValueError('invalid signed release tag')
    signed = api(repository, f'commits/{commit}')
    if signed['sha'] != commit or not signed['commit']['verification']['verified']:
        raise ValueError('release tag requires a verified source commit')
    return commit


def select(repository, tag):
    commit = tag_commit(repository, tag)
    producer = os.environ.get('PRODUCER_RUN')
    if producer:
        if not re.fullmatch(r'[0-9]+', producer) or producer != os.environ['GITHUB_RUN_ID']:
            raise ValueError('the normal release must use its own qualification run')
        candidates = [api(repository, f'actions/runs/{producer}')]
    else:
        response = api(repository, 'actions/workflows/sdk-release.yml/runs',
                       '-X', 'GET', '-f', 'event=push', '-f', f'head_sha={commit}')
        candidates = response['workflow_runs']
    qualified = []
    for run in candidates:
        if (run['event'] != 'push' or run['path'] != '.github/workflows/sdk-release.yml'
                or run['head_sha'] != commit or run['head_branch'] != tag
                or run['head_repository']['full_name'] != repository):
            continue
        if not producer and (run['status'] != 'completed'
                             or run['conclusion'] not in {'success', 'failure'}):
            continue
        items = api(repository, f"actions/runs/{run['id']}/artifacts", '-f', 'per_page=100',
                    '-X', 'GET')['artifacts']
        sdk = [item for item in items if item['name'] in SDK_ARTIFACTS and not item['expired']]
        if (len(sdk) == len(SDK_ARTIFACTS) and {item['name'] for item in sdk} == SDK_ARTIFACTS
                and all(re.fullmatch(r'sha256:[0-9a-f]{64}', item.get('digest') or '')
                        for item in sdk)):
            qualified.append(run['id'])
    if len(qualified) != 1:
        raise ValueError('expected exactly one trusted tag-push run with both SDK artifacts')
    print(f'commit={commit}\nrun={qualified[0]}')


def publish(repository, tag):
    commit = tag_commit(repository, tag)
    if commit != os.environ['OS_COMMIT']:
        raise ValueError('release tag changed after qualification')
    runtime = Path('runtime-source').resolve()
    head = subprocess.check_output(['git', '-C', runtime, 'rev-parse', 'HEAD'], text=True)
    if head.strip() != commit:
        raise ValueError('published assets must be validated at the original tag')
    subprocess.run(['git', '-C', runtime, 'diff', '--exit-code', commit], check=True)
    assets = Path('build/release-assets').resolve()
    # The immutable tag's code defines the inventory and validates all linked inputs.
    check = """
import sys
from pathlib import Path
from compose_ci import RELEASE_FILES, release_platform
from build import sha
assets = Path(sys.argv[1])
sums = ''.join(f'{sha(assets / name)}  {name}\\n' for name in RELEASE_FILES)
(assets / 'RELEASE-SHA256SUMS').write_text(sums)
release_platform(assets, sys.argv[2])
print(*RELEASE_FILES, 'RELEASE-SHA256SUMS', sep='\\n')
"""
    names = subprocess.check_output(
        [sys.executable, '-c', check, assets, commit], cwd=runtime,
        env={**os.environ, 'PYTHONPATH': str(runtime / 'os')}, text=True).splitlines()
    files = {name: assets / name for name in names}
    if {file.name for file in assets.iterdir()} != set(files):
        raise ValueError('local asset inventory differs from the release contract')
    expected = {}
    for name, file in files.items():
        with file.open('rb') as stream:
            expected[name] = (file.stat().st_size,
                              'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest())

    def verify_assets(rows):
        names_seen = set()
        for row in rows:
            name = row['name']
            if (name in names_seen or expected.get(name) != (row['size'], row['digest'])
                    or row['state'] != 'uploaded'):
                raise ValueError(f'unexpected or differing release asset: {name}')
            names_seen.add(name)
        return names_seen

    releases = json.loads(gh('release', 'list', '--repo', repository, '--limit', '100',
                             '--json', 'tagName,isDraft,isPrerelease'))
    existing = next((item for item in releases if item['tagName'] == tag), None)
    if existing is None:
        gh('release', 'create', tag, '--repo', repository, '--draft', '--prerelease',
           '--latest=false', '--verify-tag', '--title', f'Q2 OS {tag}',
           '--notes', f'Qualified Q2 SDK, userspace and production kernel for {commit}.')
    elif not existing['isPrerelease']:
        raise ValueError('existing release is not a prerelease')
    release_id = gh('release', 'view', tag, '--repo', repository, '--json', 'databaseId',
                    '--jq', '.databaseId').strip()
    endpoint = f'releases/{release_id}/assets'
    present = verify_assets(api(repository, endpoint))
    if existing is not None and not existing['isDraft']:
        if present != set(files):
            raise ValueError('published release inventory differs')
        return
    missing = [path for name, path in files.items() if name not in present]
    if missing:
        gh('release', 'upload', tag, *missing, '--repo', repository)
    if verify_assets(api(repository, endpoint)) != set(files):
        raise ValueError('draft release inventory is incomplete')
    if tag_commit(repository, tag) != commit:
        raise ValueError('release tag changed before publication')
    gh('release', 'edit', tag, '--repo', repository, '--draft=false', '--prerelease',
       '--latest=false')


if __name__ == '__main__':
    command = {'select': select, 'publish': publish}[sys.argv[1]]
    command(os.environ['GITHUB_REPOSITORY'], os.environ['RC_TAG'])
