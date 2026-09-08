#!/usr/bin/env python3
"""Run a read-only SQL query against the metaserver analytics database."""

from __future__ import annotations

import json
import sqlite3
import sys


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: metaserver-sqlite SQL [PARAM ...]")

    sql = sys.argv[1].lstrip()
    if not sql.lower().startswith(("select", "with", "pragma", "explain")):
        raise SystemExit("only read-only SQL queries are allowed")

    connection = sqlite3.connect(
        "file:/var/www/data/games.sqlite?mode=ro", uri=True, timeout=5
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    cursor = connection.execute(sql, sys.argv[2:])
    json.dump([dict(row) for row in cursor], sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
