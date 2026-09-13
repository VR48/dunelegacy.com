#!/usr/bin/env python3
"""Private, bounded SQLite worker for PHP installations without pdo_sqlite.

Only the PHP service starts this process, passing its fixed database path.
SQL comes from service code; client values remain bound parameters. Keeping one
connection alive preserves BEGIN IMMEDIATE across each reservation transaction.
No credentials or feedback text are passed to this helper.
"""
import json
import os
import sqlite3
import sys


def main():
    os.umask(0o007)
    db = sqlite3.connect(sys.argv[1], timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        while True:
            line = sys.stdin.buffer.readline(32769)
            if not line:
                break
            if len(line) > 32768 or not line.endswith(b'\n'):
                break
            try:
                request = json.loads(line)
                cursor = db.execute(request['sql'], request.get('params', []))
                rows = cursor.fetchmany(501) if cursor.description else []
                if len(rows) > 500:
                    raise ValueError('result too large')
                result = json.dumps({'ok': True, 'rows': [dict(row) for row in rows]})
                if len(result.encode()) > 65536:
                    raise ValueError('response too large')
            except Exception:
                result = '{"ok":false}'
            print(result, flush=True)
    finally:
        db.close()  # Also rolls back an unfinished transaction on EOF/process exit.


if __name__ == '__main__':
    main()
