#!/usr/bin/env python3
"""Read pinned preview identity and one own-room lifecycle window without modifying service state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

COLUMNS = 'room_id,event_id,kind,occurred_at,participant_id,client_runtime,game_version,reason,transport,source'


def verify_preview(target: Path, source: str, snapshot: str) -> dict:
    if any(path.is_symlink() for path in [target, target / 'releases', target / 'run']):
        raise ValueError('Private preview directories must not be symlinks')
    release = (target / 'current').resolve(strict=True)
    if release.parent != (target / 'releases').resolve(strict=True) or release.name != snapshot:
        raise ValueError('Preview current must select its expected private immutable snapshot')
    raw_manifest = (release / 'SOURCE.json').read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != snapshot:
        raise ValueError('Installed preview manifest does not match the expected snapshot')
    manifest = json.loads(raw_manifest)
    if manifest['commit'] != source:
        raise ValueError('Installed preview does not match requested source')
    expected = manifest['sha256']
    actual_files = {str(path.relative_to(release)) for path in release.rglob('*') if path.is_file()}
    if actual_files != set(expected) | {'SOURCE.json'}:
        raise ValueError('Unexpected or missing live preview files')
    actual = {}
    for name, digest in expected.items():
        path = release / name
        if (not re.fullmatch(r'(?:src|public)/[A-Za-z0-9_.-]+', name) or path.is_symlink()
                or not path.resolve().is_relative_to(release) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('Invalid preview manifest path or hash')
        actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError('Live preview source hash mismatch')
    return {'sourceCommit': source, 'serviceSnapshot': snapshot, 'actualSourceHashes': actual}


def read_rows(database: Path, room: str, since: int, until: int) -> list:
    if database.is_symlink():
        raise ValueError('Preview database must not be a symlink')
    with sqlite3.connect(database.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT ' + COLUMNS + ' FROM analytics_relay_events '
                          'WHERE room_id=? AND occurred_at BETWEEN ? AND ? ORDER BY occurred_at,event_id',
                          (room, since, until)).fetchall()
        return [dict(row) for row in rows]


def own_room(database: Path, app_version: str, since: int, until: int) -> str:
    if database.is_symlink():
        raise ValueError('Preview database must not be a symlink')
    with sqlite3.connect(database.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        rooms = db.execute("SELECT DISTINCT room_id FROM analytics_relay_events WHERE kind='joined' "
                           'AND game_version=? AND occurred_at BETWEEN ? AND ?',
                           (app_version, since, until)).fetchall()
    if len(rooms) != 1:
        raise ValueError('Own-run app version and UTC window must identify exactly one preview room')
    return rooms[0][0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--app-version', default='')
    parser.add_argument('--since', type=int, required=True)
    parser.add_argument('--until', type=int, required=True)
    args = parser.parse_args()
    if (not re.fullmatch(r'play-test-[a-z0-9-]{1,80}', args.name)
            or not re.fullmatch(r'[0-9a-f]{40}', args.source)
            or not re.fullmatch(r'[0-9a-f]{9,40}', args.name.rsplit('-', 1)[1])
            or not args.source.startswith(args.name.rsplit('-', 1)[1])):
        raise ValueError('Invalid immutable preview identity')
    if not re.fullmatch(r'[0-9a-f]{64}', args.snapshot):
        raise ValueError('Invalid expected preview service snapshot')
    now = int(time.time())
    if not now - 86400 <= args.since <= args.until <= now + 300 or args.until - args.since > 3600:
        raise ValueError('Evidence requires a bounded own-run UTC window within the past day')
    if args.app_version and not re.fullmatch(r'[A-Za-z0-9._-]{1,32}', args.app_version):
        raise ValueError('Invalid observed own-run app version')
    result = {'since': args.since, 'until': args.until}
    private_root = Path('/var/www/data/dunecity-p2p-previews')
    if private_root.is_symlink():
        raise ValueError('Private preview root must not be a symlink')
    target = private_root / args.name
    result.update({'preview': args.name, **verify_preview(target, args.source, args.snapshot)})
    if args.app_version:
        database = target / 'run/games.sqlite'
        room = own_room(database, args.app_version, args.since, args.until)
        result['directLifecycle'] = read_rows(database, room, args.since, args.until)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
