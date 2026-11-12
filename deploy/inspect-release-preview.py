#!/usr/bin/env python3
"""Read only pinned preview/own-room evidence; optionally request a self-expiring preview outage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

COLUMNS = 'room_id,event_id,kind,occurred_at,participant_id,client_runtime,game_version,reason,transport,source'


def read_rows(database: Path, room: str, since: int, until: int) -> list:
    with sqlite3.connect(database.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT ' + COLUMNS + ' FROM analytics_relay_events '
                          'WHERE room_id=? AND occurred_at BETWEEN ? AND ? ORDER BY occurred_at,event_id',
                          (room, since, until)).fetchall()
        return [dict(row) for row in rows]


def own_room(database: Path, app_version: str, since: int, until: int) -> str:
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
    parser.add_argument('--app-version', default='')
    parser.add_argument('--since', type=int, required=True)
    parser.add_argument('--until', type=int, required=True)
    parser.add_argument('--outage-seconds', type=int, choices=(0, 60), default=0)
    args = parser.parse_args()
    if (not re.fullmatch(r'play-test-[a-z0-9-]{1,80}', args.name)
            or not re.fullmatch(r'[0-9a-f]{40}', args.source)
            or not re.fullmatch(r'[0-9a-f]{9,40}', args.name.rsplit('-', 1)[1])
            or not args.source.startswith(args.name.rsplit('-', 1)[1])):
        raise ValueError('Invalid immutable preview identity')
    now = int(time.time())
    if not now - 86400 <= args.since <= args.until <= now + 300 or args.until - args.since > 3600:
        raise ValueError('Evidence requires a bounded own-run UTC window within the past day')
    if args.app_version and not re.fullmatch(r'[A-Za-z0-9._-]{1,32}', args.app_version):
        raise ValueError('Invalid observed own-run app version')
    result = {'since': args.since, 'until': args.until}
    target = Path('/var/www/data/dunecity-p2p-previews') / args.name
    current = target / 'current'
    raw_manifest = (current / 'SOURCE.json').read_bytes()
    manifest = json.loads(raw_manifest)
    if manifest['commit'] != args.source:
        raise ValueError('Installed preview does not match requested source')
    actual = {name: hashlib.sha256((current / name).read_bytes()).hexdigest() for name in manifest['sha256']}
    if actual != manifest['sha256']:
        raise ValueError('Live preview source hash mismatch')
    result.update({'preview': args.name, 'sourceCommit': args.source,
                   'serviceSnapshot': hashlib.sha256(raw_manifest).hexdigest(),
                   'actualSourceHashes': actual})
    if args.app_version:
        database = target / 'run/games.sqlite'
        room = own_room(database, args.app_version, args.since, args.until)
        result['directLifecycle'] = read_rows(database, room, args.since, args.until)
    if args.outage_seconds:
        if not args.app_version:
            raise ValueError('An outage requires an existing own preview room')
        flag = target / 'outage-until'
        if flag.is_symlink():
            raise ValueError('Preview outage flag must not be a symlink')
        staged = target / ('.outage-' + str(os.getpid()))
        fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        try:
            os.fchown(fd, -1, target.stat().st_gid)
            os.fchmod(fd, 0o640)
            with os.fdopen(fd, 'w') as handle:
                handle.write(str(now + args.outage_seconds) + '\n')
            staged.replace(flag)
        finally:
            if staged.exists():
                staged.unlink()
        result['previewOutageUntil'] = now + args.outage_seconds
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
