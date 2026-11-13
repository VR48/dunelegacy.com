#!/usr/bin/env python3
"""Deliver one frozen GitHub staging publication without storing browser binaries in Git."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request

from play_parts import artifact_sha256, join

REPOSITORY = 'dunecity-project/dunecity'
CHANNEL = 'play-staging'
PUBLICATION = 'publication-inputs.json'
BROWSER = 'DuneCity-browser.tar.gz'
SERVICE = 'DuneCity-p2p-service.tar.gz'
REQUEST = 'staging-browser.json'
FIELDS = {'schema', 'websiteCodeCommit', 'repository', 'releaseTag', 'sourceCommit',
          'publicationManifestSha256', 'browserArchiveSha256', 'serviceArchiveSha256'}
CI_JOBS = {'test-linux', 'relay-tests', 'signaling-tests', 'build-emscripten',
           'build-windows', 'build-linux', 'build-macos'}
CONNECTIONS = 'https://dunelegacy.com/play-staging/ https://api.github.com https://raw.githubusercontent.com'
BOOTSTRAP = """'use strict';
// Use the game's existing validated page-URL endpoint seam, never the production lobby.
const stagingURL = new URL(window.location.href);
stagingURL.searchParams.set('relay', 'https://dunelegacy.com/play-staging/p2p');
stagingURL.searchParams.delete('relaydev');
window.history.replaceState(null, '', stagingURL);
"""


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def request_inputs(path, website_revision=None):
    request = json.loads(path.read_text())
    if set(request) != FIELDS or request['schema'] != 1 or request['repository'] != REPOSITORY:
        raise ValueError('Unexpected staging request schema/repository')
    for key in ('websiteCodeCommit', 'sourceCommit'):
        if not isinstance(request[key], str) or not re.fullmatch(r'[0-9a-f]{40}', request[key]):
            raise ValueError('Staging request requires full source/code SHAs')
    for key in ('publicationManifestSha256', 'browserArchiveSha256', 'serviceArchiveSha256'):
        if not isinstance(request[key], str) or not re.fullmatch(r'[0-9a-f]{64}', request[key]):
            raise ValueError('Staging request requires immutable artifact hashes')
    if not re.fullmatch('staging-' + request['sourceCommit'] + r'-[0-9a-f]{12}', request['releaseTag']):
        raise ValueError('Staging request cannot select a stable/mutable release')
    if website_revision is not None and request['websiteCodeCommit'] != website_revision:
        raise ValueError('Staging artifact must use its frozen website code SHA')
    return request


def api(path):
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    with urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + '/' + path,
                                                      headers=headers), timeout=60) as response:
        return json.load(response)


def validate_receipts(manifest):
    source = manifest['sourceCommit']
    workflow = '.github/workflows/build.yml'
    for qualification in (True, False):
        run_id = int(manifest['qualificationRunId'] if qualification else manifest['runId'])
        attempt = int(manifest['qualificationRunAttempt'] if qualification else manifest['runAttempt'])
        if run_id <= 0 or attempt <= 0:
            raise ValueError('Invalid staging producer/qualification receipt')
        prefix = f'actions/runs/{run_id}/attempts/{attempt}'
        run = api(prefix)
        if (run['status'] != 'completed' or run['path'] != workflow
                or run['run_attempt'] != attempt or run['repository']['full_name'] != REPOSITORY):
            raise ValueError('Staging producer/qualification is not independently trusted')
        if qualification:
            valid = (run['conclusion'] == 'success' and run['event'] == 'push'
                     and run['head_branch'] == 'integrate/feature-batch'
                     and run['head_sha'] == source == manifest['qualificationSourceCommit'])
        else:
            valid = (run['id'] == run_id and run['event'] in ('schedule', 'workflow_dispatch')
                     and run['head_branch'] in ('main', 'integrate/feature-batch')
                     and (run['event'] != 'schedule' or run['head_branch'] == 'main')
                     and run['head_sha'] == manifest['producerSourceCommit']
                     and run['event'] == manifest['producerEvent']
                     and run['head_branch'] == manifest['producerHeadBranch']
                     and run['path'] == manifest['producerWorkflowPath'])
        jobs = api(prefix + '/jobs?per_page=100')['jobs']
        if not valid:
            raise ValueError('Staging producer/qualification source is not independently trusted')
        if qualification:
            if not CI_JOBS.issubset({job['name'] for job in jobs if job['conclusion'] == 'success'}):
                raise ValueError('Staging qualification has missing or failed required jobs')
            continue
        outcomes = {name: [job['conclusion'] for job in jobs if job['name'] == name]
                    for name in CI_JOBS | {'dev-release'}}
        if any(outcomes[name] != ['success'] for name in CI_JOBS):
            raise ValueError('Staging producer has missing or failed required jobs at the frozen attempt')
        if run['conclusion'] == 'success' and outcomes['dev-release'] == ['success']:
            return
        if run['conclusion'] != 'failure' or outcomes['dev-release'] != ['failure']:
            raise ValueError('Staging producer has no successful publication receipt')
        # Keep the immutable package attempt; a later publication-only retry may
        # inherit its successful inputs but cannot override an explicit red job.
        publication = api(f'actions/runs/{run_id}')
        if (publication['id'] != run_id or publication['run_attempt'] <= attempt
                or publication['status'] != 'completed' or publication['conclusion'] != 'success'
                or publication['repository']['full_name'] != REPOSITORY
                or any(publication[key] != run[key] for key in
                       ('path', 'head_sha', 'head_branch', 'event'))):
            raise ValueError('Staging producer publication retry is not independently successful/trusted')
        retry = publication['run_attempt']
        retry_jobs = api(f'actions/runs/{run_id}/attempts/{retry}/jobs?per_page=100')['jobs']
        if ([job['conclusion'] for job in retry_jobs if job['name'] == 'dev-release'] != ['success']
                or any(job['name'] in CI_JOBS and job['conclusion'] != 'success' for job in retry_jobs)):
            raise ValueError('Staging producer publication retry contains failed/skipped required jobs')


def fetch(request_path, destination, website_revision):
    request = request_inputs(request_path, website_revision)
    tag = request['releaseTag']
    release = api('releases/tags/' + urllib.parse.quote(tag, safe=''))
    if release['draft'] or not release['prerelease'] or release['tag_name'] != tag:
        raise ValueError('Staging requires a published immutable prerelease')
    ref = api('git/ref/tags/' + tag)['object']
    for _ in range(4):
        if ref['type'] != 'tag':
            break
        ref = api('git/tags/' + ref['sha'])['object']
    if ref['type'] != 'commit' or ref['sha'] != request['sourceCommit']:
        raise ValueError('Published staging tag differs from the frozen source')
    relation = api('compare/' + request['sourceCommit'] + '...integrate/feature-batch')['status']
    if relation not in ('ahead', 'identical'):
        raise ValueError('Staging source is outside trusted integration history')
    assets = {item['name']: item for item in release['assets']}
    destination.mkdir(parents=True, exist_ok=True)
    for name, key in ((PUBLICATION, 'publicationManifestSha256'), (BROWSER, 'browserArchiveSha256'),
                      (SERVICE, 'serviceArchiveSha256')):
        asset = assets.get(name, {})
        if asset.get('size', 0) <= 0 or asset.get('digest') != 'sha256:' + request[key]:
            raise ValueError('Missing/corrupt frozen staging asset: ' + name)
        target = destination / name
        if not target.exists():
            url = 'https://github.com/' + REPOSITORY + '/releases/download/' + tag + '/' + name
            with urllib.request.urlopen(url, timeout=180) as response, target.open('xb') as output:
                shutil.copyfileobj(response, output, length=1 << 20)
        if target.stat().st_size != asset['size'] or digest(target) != request[key]:
            raise ValueError('Frozen staging download hash mismatch: ' + name)
    shutil.copyfile(request_path, destination / REQUEST)
    manifest = verify_inputs(destination, website_revision)
    validate_receipts(manifest)
    write_json(destination / 'release.json', release)


def verify_inputs(inputs, website_revision):
    request = request_inputs(inputs / REQUEST, website_revision)
    for name, key in ((PUBLICATION, 'publicationManifestSha256'), (BROWSER, 'browserArchiveSha256'),
                      (SERVICE, 'serviceArchiveSha256')):
        if digest(inputs / name) != request[key]:
            raise ValueError('Frozen staging input hash mismatch: ' + name)
    manifest = json.loads((inputs / PUBLICATION).read_text())
    identity = hashlib.sha256((request['sourceCommit'] + '\n' + manifest['recipe']).encode()).hexdigest()
    if (manifest['schema'] != 1 or manifest['repository'] != REPOSITORY or manifest['releaseBuild'] != 'OFF'
            or manifest['tag'] != request['releaseTag'] or manifest['sourceCommit'] != request['sourceCommit']
            or manifest['inputIdentity'] != identity or request['releaseTag'].rsplit('-', 1)[1] != identity[:12]
            or manifest['sha256'].get(BROWSER) != request['browserArchiveSha256']
            or manifest['sha256'].get(SERVICE) != request['serviceArchiveSha256']):
        raise ValueError('Staging manifest/source/identity does not match the frozen request')
    if any(name.startswith(('updates-', 'appcast-')) for name in manifest['sha256']):
        raise ValueError('Staging publication cannot carry stable update feeds')
    return manifest


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=False)
    seen = set()
    with tarfile.open(archive) as source:
        for member in source:
            name = Path(member.name)
            if (name.is_absolute() or '..' in name.parts or member.issym() or member.islnk()
                    or not (member.isfile() or member.isdir()) or name in seen):
                raise ValueError('Unsafe/duplicate staging archive member')
            seen.add(name)
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, target.open('xb') as output:
                    shutil.copyfileobj(stream, output, length=1 << 20)


def prepare(inputs, repository, website, website_revision):
    manifest = verify_inputs(inputs, website_revision)
    play = website / CHANNEL
    extract(inputs / BROWSER, play)
    service = website.parent / 'p2p-staging'
    extract(inputs / SERVICE, service)
    source = json.loads((service / 'SOURCE.json').read_text())
    if source['commit'] != manifest['sourceCommit']:
        raise ValueError('Staging browser and signaling source must match')
    build_path = play / 'build.json'
    original = build_path.read_bytes()
    build = json.loads(original)
    if (build['sourceCommit'] != manifest['sourceCommit'] or build['version'] != manifest['version']
            or build['releaseBuild'] != 'OFF' or build['channel'] != 'staging'
            or build['cacheScope'] != '/play-staging/' or 'dev+' + manifest['sourceCommit'][:12] not in build['displayVersion']):
        raise ValueError('Frozen browser is not the isolated dev-identity staging package')
    required = {'index.html', 'shell.js', 'shell.css', 'p2p-direct.js', 'dunecity.js', 'dunecity.wasm', 'dunecity.data'}
    if not required.issubset(build['artifacts']) or set(build['artifacts']) != set(build['sha256']):
        raise ValueError('Incomplete staging browser runtime')
    for name, checksum in build['sha256'].items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or artifact_sha256(play, build, name) != checksum:
            raise ValueError('Frozen staging browser artifact hash mismatch')
    expected_files = {'build.json', '.htaccess'}
    for name in build['artifacts']:
        expected_files.update((build.get('split') or {}).get(name, [name]))
    if {path.relative_to(play).as_posix() for path in play.rglob('*') if path.is_file()} != expected_files:
        raise ValueError('Staging browser archive contains unlisted runtime files')
    join(play)
    (play / 'source-build.json').write_bytes(original)
    shutil.copyfile(inputs / PUBLICATION, play / PUBLICATION)
    (play / 'staging-bootstrap.js').write_text(BOOTSTRAP)
    index = play / 'index.html'
    html = index.read_text()
    html, count = re.subn(r'(?=<script\b[^>]*\bsrc=[\"\']?shell\.js(?:[?\"\'\s>]))',
                          '<script src="staging-bootstrap.js"></script>', html)
    if count != 1 or 'Staging ' + build['displayVersion'] not in html:
        raise ValueError('Expected one staging shell and visible dev/source identity')
    for path, text in ((index, html), (play / '.htaccess', (play / '.htaccess').read_text())):
        text, count = re.subn(r'connect-src [^;"<>]+;', 'connect-src ' + CONNECTIONS + ';', text)
        if count != 1:
            raise ValueError('Expected exactly one staging connect-src policy')
        path.write_text(text)
    routes = play / 'p2p'
    routes.mkdir()
    shutil.copyfile(service / 'public/.htaccess', routes / '.htaccess')
    shutil.copyfile(repository / 'deploy/p2p-staging-entry.php', routes / 'index.php')
    build.pop('split', None)
    build.update(signalingOrigins=['https://dunelegacy.com/play-staging/'],
                 signalingBaseUrl='https://dunelegacy.com/play-staging/p2p',
                 signalingSourceCommit=source['commit'], signalingManifestSha256=digest(service / 'SOURCE.json'),
                 matchStats='blocked-by-channel-csp', delivery=request_inputs(inputs / REQUEST, website_revision))
    for name in ('source-build.json', PUBLICATION, 'staging-bootstrap.js'):
        build['artifacts'].append(name)
    for name in build['artifacts']:
        build['sha256'][name] = digest(play / name)
    write_json(build_path, build)
    source_policy = repository / 'website/.htaccess'
    target_policy = website / '.htaccess'
    if source_policy.is_symlink() or target_policy.is_symlink():
        raise ValueError('Staging website policy must not be a symlink')
    # The remote archive already contains the policy in its output website.
    # Only independent output trees need a copy from the frozen repository.
    if (repository / 'website').resolve(strict=True) != website.resolve(strict=True):
        shutil.copyfile(source_policy, target_policy)
    subprocess.run([sys.executable, str(repository / 'deploy/check-web-security.py'),
                    '--website-root', str(website), '--play-root', str(play),
                    '--service-root', str(service), '--public-routes', str(routes / '.htaccess')],
                   stdout=sys.stderr, check=True)
    return build


def install_service(service, repository, private_root, group=None):
    spec = importlib.util.spec_from_file_location('staging_p2p_installer', repository / 'deploy/install-p2p-service.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    os.umask(0o027)
    snapshot = installer.install(service, private_root, group, base_path='/play-staging/p2p')
    private_root = private_root.resolve(strict=True)
    config = json.loads(subprocess.check_output(['php', '-r', 'echo json_encode(require $argv[1]);',
                                               str(private_root / 'config.php')], text=True))
    expected = dict(state_dir=str(private_root / 'run/state'), base_path='/play-staging/p2p',
                    public_base_url='https://dunelegacy.com/play-staging/p2p',
                    allowed_origins=['https://dunelegacy.com'], required_game_protocol=0,
                    allow_plaintext_loopback=False, analytics_enabled=True)
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError('Preserved staging config differs from isolated deployment contract')
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    download = commands.add_parser('fetch', help='Download only the frozen, independently green publication')
    download.add_argument('--request', type=Path, required=True)
    download.add_argument('--inputs', type=Path, required=True)
    download.add_argument('--website-revision', required=True)
    stage = commands.add_parser('prepare', help='Verify and derive only the isolated temporary staging website')
    stage.add_argument('--inputs', type=Path, required=True)
    stage.add_argument('--repository', type=Path, required=True)
    stage.add_argument('--website', type=Path, required=True)
    stage.add_argument('--website-revision', required=True)
    service = commands.add_parser('install-service', help='Install only the separate staging signaling state/code')
    service.add_argument('--source', type=Path, required=True)
    service.add_argument('--repository', type=Path, required=True)
    service.add_argument('--target', type=Path, default=Path('/var/www/data/dunecity-p2p-staging'))
    service.add_argument('--group')
    args = parser.parse_args()
    if args.command == 'fetch':
        fetch(args.request, args.inputs, args.website_revision)
    elif args.command == 'prepare':
        print(json.dumps(prepare(args.inputs, args.repository, args.website, args.website_revision)))
    else:
        print(install_service(args.source, args.repository, args.target, args.group))
