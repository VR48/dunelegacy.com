#!/usr/bin/env python3
"""Export the latest completed analytics match as JSON for an admin audit."""

from __future__ import annotations

import json
import sqlite3
import sys


def row_dict(cursor: sqlite3.Cursor, row: sqlite3.Row | None):
    if row is None:
        return None
    return {description[0]: row[index] for index, description in enumerate(cursor.description)}


def rows_dict(cursor: sqlite3.Cursor):
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def parsed_json(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: export-latest-analytics.py DATABASE")

    connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=5)
    match_cursor = connection.execute(
        """SELECT * FROM analytics_matches
           WHERE ended_at IS NOT NULL
           ORDER BY ended_at DESC, updated_at DESC
           LIMIT 1"""
    )
    match = row_dict(match_cursor, match_cursor.fetchone())
    if match is None:
        raise SystemExit("no completed analytics match found")

    match_id = match["match_id"]
    match["start_json"] = parsed_json(match["start_json"])
    match["end_json"] = parsed_json(match["end_json"])

    player_cursor = connection.execute(
        "SELECT * FROM analytics_players WHERE match_id = ? ORDER BY slot", (match_id,)
    )
    players = rows_dict(player_cursor)
    for player in players:
        item_cursor = connection.execute(
            """SELECT item_id, item_name, item_kind, produced, killed, lost
               FROM analytics_player_items
               WHERE match_id = ? AND slot = ?
               ORDER BY item_kind, item_id""",
            (match_id, player["slot"]),
        )
        player["item_stats"] = rows_dict(item_cursor)
        qbot_cursor = connection.execute(
            """SELECT item_id, item_name, target_weight_bps, built, lost, destroyed,
                      reward_milli, lost_value, damage_value_milli, kill_bonus_milli
               FROM analytics_qbot_units
               WHERE match_id = ? AND slot = ?
               ORDER BY item_id""",
            (match_id, player["slot"]),
        )
        player["qbot_units"] = rows_dict(qbot_cursor)

    json.dump({"match": match, "players": players}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
