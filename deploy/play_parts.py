#!/usr/bin/env python3
"""Join browser artifacts the game packager split to stay under GitHub's 100 MB file limit.

build.json may list `"split": {"dunecity.data": ["dunecity.data.part-000", ...]}`. The parts,
concatenated in order, are the artifact whose SHA-256 build.json records. The repository keeps
only the parts; every consumer that needs the bytes (compression in CI, the staged web root on
the server) joins them first.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

PART_NAME = re.compile(r'[A-Za-z0-9._-]+\.part-[0-9]{3}')


def split_parts(play, build, name):
    """The part paths of a split artifact, or None when it is stored whole."""
    parts = (build.get('split') or {}).get(name)
    if parts is None:
        return None
    if (not isinstance(parts, list) or not parts
            or any(not isinstance(part, str) or not PART_NAME.fullmatch(part)
                   or not part.startswith(name + '.part-') for part in parts)):
        raise ValueError(f'Invalid split parts for {name}')
    return [play / part for part in parts]


def artifact_sha256(play, build, name):
    """SHA-256 of an artifact, read whole or from its parts in order."""
    digest = hashlib.sha256()
    for path in split_parts(play, build, name) or [play / name]:
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b''):
                digest.update(chunk)
    return digest.hexdigest()


def join(play, keep_parts=False):
    build = json.loads((play / 'build.json').read_text(encoding='utf-8-sig'))
    joined = []
    for name in (build.get('split') or {}):
        parts = split_parts(play, build, name)
        if artifact_sha256(play, build, name) != build['sha256'][name]:
            raise ValueError(f'Split parts of {name} do not match build.json')
        temporary = play / (name + '.joining')
        with temporary.open('wb') as output:
            for part in parts:
                with part.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b''):
                        output.write(chunk)
        temporary.replace(play / name)
        if not keep_parts:
            for part in parts:
                part.unlink()
        joined.append(name)
    return joined


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('play', type=Path)
    parser.add_argument('--keep-parts', action='store_true',
                        help='Leave the parts in place (CI workspace); default removes them')
    args = parser.parse_args()
    for name in join(args.play, args.keep_parts):
        print(f'Joined {name}')
