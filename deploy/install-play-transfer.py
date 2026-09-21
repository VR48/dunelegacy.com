#!/usr/bin/env python3
"""Install only precompressed files matching the exact staged browser release."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

NAMES = ('dunecity.data', 'dunecity.wasm', 'dunecity.js',
         'p2p-direct.js', 'shell.js', 'shell.css')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(source, play):
    manifest = json.loads((source / 'transfer.json').read_text())
    # Validate everything before copying anything. A concurrent release must not
    # pair an old compressed response with newer JavaScript or game data.
    if set(manifest) != set(NAMES):
        raise ValueError('Unexpected browser transfer manifest')
    for name in NAMES:
        if digest(play / name) != manifest[name]['sourceSha256']:
            raise ValueError(f'Browser release changed while preparing {name}; rerun deployment')
        if digest(source / (name + '.br')) != manifest[name]['encodedSha256']:
            raise ValueError(f'Corrupt compressed browser artifact: {name}')
    for name in NAMES:
        shutil.copyfile(source / (name + '.br'), play / (name + '.br'))
    # Kept in deploy/ so publishing another game package cannot remove the rule.
    rules = Path(__file__).with_name('play-transfer.htaccess').read_text()
    with (play / '.htaccess').open('a') as headers:
        headers.write('\n' + rules)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--play', type=Path, required=True)
    args = parser.parse_args()
    install(args.source, args.play)
