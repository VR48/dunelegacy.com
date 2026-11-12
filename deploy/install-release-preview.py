#!/usr/bin/env python3
"""Install the one explicitly committed release preview without changing production /play or /p2p."""
import argparse
import grp
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from play_parts import join


def preview_spec(repository: Path, website: Path) -> dict:
    descriptor = json.loads((repository / 'deploy/release-preview.json').read_text())
    if set(descriptor) != {'path', 'sourceCommit', 'version', 'gameProtocol'}:
        raise ValueError('Unexpected preview descriptor fields')
    name, source = descriptor['path'], descriptor['sourceCommit']
    if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}', source):
        raise ValueError('Invalid preview source commit')
    if (not isinstance(name, str) or not re.fullmatch(r'play-test-[a-z0-9-]{1,80}', name)
            or not re.fullmatch(r'play-test-.+-' + source[:9] + r'[0-9a-f]{0,31}', name)
            or not source.startswith(name.rsplit('-', 1)[1])):
        raise ValueError('Preview path must include its immutable source prefix')
    protocol = descriptor['gameProtocol']
    if type(protocol) is not int or not 1 <= protocol <= 65535:
        raise ValueError('Invalid preview game protocol')
    if not isinstance(descriptor['version'], str) or not re.fullmatch(r'\d+\.\d+\.\d+', descriptor['version']):
        raise ValueError('Invalid preview version')
    play = website / name
    if play.is_symlink() or not play.resolve().is_relative_to(website.resolve()):
        raise ValueError('Preview must remain within the staged website')
    service = repository / 'p2p-preview'
    manifest = json.loads((service / 'SOURCE.json').read_text())
    build = json.loads((play / 'build.json').read_text(encoding='utf-8-sig'))
    if manifest['commit'] != source or build['sourceCommit'] != source:
        raise ValueError('Preview browser and signaling source commits must match')
    if build['version'] != descriptor['version']:
        raise ValueError('Preview browser version must match the descriptor')
    if (play / 'p2p/.htaccess').read_bytes() != (service / 'public/.htaccess').read_bytes():
        raise ValueError('Preview signaling routes do not match the pinned source')
    if (play / 'p2p/index.php').read_bytes() != (repository / 'deploy/p2p-preview-entry.php').read_bytes():
        raise ValueError('Preview entrypoint differs from the reviewed deployment wrapper')
    connections = re.findall(r'\bconnect-src\s+([^;"]+)', (play / '.htaccess').read_text())
    allowed = {'https://dunelegacy.com/' + name + '/',
               'https://api.github.com', 'https://raw.githubusercontent.com'}
    if len(connections) != 1 or set(connections[0].split()) != allowed:
        raise ValueError('Preview connect-src must exclude production services and analytics')
    return descriptor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--website', type=Path, required=True)
    parser.add_argument('--group')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    descriptor = preview_spec(args.repository, args.website)
    service = args.repository / 'p2p-preview'
    play = args.website / descriptor['path']
    subprocess.run([sys.executable, str(args.repository / 'deploy/check-web-security.py'),
                    '--website-root', str(args.website), '--play-root', str(play),
                    '--service-root', str(service), '--public-routes', str(play / 'p2p/.htaccess')], check=True)
    if args.validate_only:
        print(json.dumps({'preview': descriptor, 'validated': True, 'installed': False}))
        return
    private_root = Path('/var/www/data/dunecity-p2p-previews')
    if private_root.is_symlink():
        raise ValueError('Private preview parent must not be a symlink')
    private_root.mkdir(exist_ok=True, mode=0o750)
    os.chown(private_root, -1, grp.getgrnam(args.group).gr_gid if args.group else os.getgid())
    private_root.chmod(0o2750)
    target = private_root / descriptor['path']
    if (target / 'current/SOURCE.json').exists():
        installed = json.loads((target / 'current/SOURCE.json').read_text())
        if installed['commit'] != descriptor['sourceCommit']:
            raise ValueError('An existing preview path cannot change source identity')
    spec = importlib.util.spec_from_file_location('installer', args.repository / 'deploy/install-p2p-service.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    os.umask(0o027)
    snapshot = installer.install(service, target, args.group,
                                 base_path='/' + descriptor['path'] + '/p2p',
                                 game_protocol=descriptor['gameProtocol'])
    config = json.loads(subprocess.check_output(
        ['php', '-r', 'echo json_encode(require $argv[1]);', str(target / 'config.php')], text=True))
    expected = {'state_dir': str(target / 'run/state'),
                'base_path': '/' + descriptor['path'] + '/p2p',
                'public_base_url': 'https://dunelegacy.com/' + descriptor['path'] + '/p2p',
                'allowed_origins': ['https://dunelegacy.com'],
                'ice_servers': ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'],
                'required_game_protocol': descriptor['gameProtocol'],
                'allow_plaintext_loopback': False, 'analytics_enabled': True}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError('Preserved preview config does not match the isolated deployment contract')
    join(play)
    print(json.dumps({'preview': descriptor, 'serviceSnapshot': snapshot, 'installed': True}))


if __name__ == '__main__':
    main()
