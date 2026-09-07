#!/usr/bin/env python3
"""Rootless SQLite backend for metaserver match summaries.

PHP PDO remains the preferred backend.  Existing droplets created before match
analytics may not have pdo_sqlite installed, while Python's standard sqlite3
module is already present for deployment tooling.  This helper accepts one
bounded JSON request on stdin and never emits or stores client log streams.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


MAX_PLAYERS = 12
MAX_UNIT_ROWS = 48
MAX_PAYLOAD_BYTES = 20 * 1024


SCHEMA = """
CREATE TABLE IF NOT EXISTS analytics_matches (
    match_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    started_at INTEGER,
    ended_at INTEGER,
    updated_at INTEGER NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'started',
    game_type TEXT,
    map_name TEXT,
    map_width INTEGER,
    map_height INTEGER,
    map_seed INTEGER,
    mod_name TEXT,
    mod_version TEXT,
    game_version TEXT,
    player_count INTEGER,
    human_count INTEGER,
    qbot_count INTEGER,
    duration_cycles INTEGER,
    duration_seconds INTEGER,
    winning_house INTEGER,
    total_spice_harvested INTEGER,
    total_units_destroyed INTEGER,
    total_structures_destroyed INTEGER,
    start_json TEXT,
    end_json TEXT
);
CREATE TABLE IF NOT EXISTS analytics_players (
    match_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    house_id INTEGER,
    house_name TEXT,
    team INTEGER,
    controller TEXT,
    qbot_difficulty TEXT,
    result TEXT,
    final_credits INTEGER,
    spice_harvested INTEGER,
    units_built INTEGER,
    structures_built INTEGER,
    units_destroyed INTEGER,
    structures_destroyed INTEGER,
    units_lost INTEGER,
    structures_lost INTEGER,
    military_value INTEGER,
    city_population INTEGER,
    city_value INTEGER,
    PRIMARY KEY (match_id, slot),
    FOREIGN KEY (match_id) REFERENCES analytics_matches(match_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS analytics_qbot_units (
    match_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    item_name TEXT,
    target_weight_bps INTEGER,
    built INTEGER,
    lost INTEGER,
    destroyed INTEGER,
    reward_milli INTEGER,
    lost_value INTEGER,
    damage_value_milli INTEGER,
    kill_bonus_milli INTEGER,
    PRIMARY KEY (match_id, slot, item_id),
    FOREIGN KEY (match_id, slot) REFERENCES analytics_players(match_id, slot) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS analytics_matches_started_idx ON analytics_matches(started_at);
CREATE INDEX IF NOT EXISTS analytics_matches_mode_idx ON analytics_matches(game_type, mod_name);
CREATE INDEX IF NOT EXISTS analytics_qbot_units_item_idx ON analytics_qbot_units(item_id);
"""


def text(value: Any, limit: int = 128) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    return str(value).strip()[:limit]


def integer(value: Any, minimum: int = -(2**31), maximum: int = 2**31 - 1) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(minimum, min(maximum, parsed))


def nested(container: Any, key: str) -> dict[str, Any]:
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, dict) else {}


def fields(payload: dict[str, Any]) -> dict[str, Any]:
    summary = nested(payload, "summary")
    map_data = nested(payload, "map")
    mod = nested(payload, "mod")
    game_type = text(payload.get("game_type"), 32)
    if game_type not in {"campaign", "skirmish", "single_custom", "multiplayer", "load"}:
        game_type = "unknown"
    outcome = text(summary.get("outcome"), 16)
    if outcome not in {"finished", "abandoned"}:
        outcome = "finished"
    return {
        "schema_version": integer(payload.get("schema_version", 0), 0, 1000) or 0,
        "game_type": game_type,
        "map_name": text(map_data.get("name", payload.get("map_name"))),
        "map_width": integer(map_data.get("width"), 0, 2048),
        "map_height": integer(map_data.get("height"), 0, 2048),
        "map_seed": integer(map_data.get("seed")),
        "mod_name": text(mod.get("name", payload.get("mod_name", "vanilla"))),
        "mod_version": text(mod.get("version")),
        "game_version": text(payload.get("game_version")),
        "player_count": integer(summary.get("player_count"), 0, MAX_PLAYERS),
        "human_count": integer(summary.get("human_count"), 0, MAX_PLAYERS),
        "qbot_count": integer(summary.get("qbot_count"), 0, MAX_PLAYERS),
        "outcome": outcome,
        "duration_cycles": integer(summary.get("duration_cycles"), 0),
        "duration_seconds": integer(summary.get("duration_seconds"), 0),
        "winning_house": integer(summary.get("winning_house"), -1, 255),
        "total_spice_harvested": integer(summary.get("total_spice_harvested"), 0),
        "total_units_destroyed": integer(summary.get("total_units_destroyed"), 0),
        "total_structures_destroyed": integer(summary.get("total_structures_destroyed"), 0),
    }


def open_database(database_path: str) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=3)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=3000")
    connection.executescript(SCHEMA)
    return connection


def store_players(connection: sqlite3.Connection, match_id: str, payload: dict[str, Any]) -> None:
    connection.execute("DELETE FROM analytics_players WHERE match_id = ?", (match_id,))
    players = payload.get("players")
    if not isinstance(players, list):
        return
    player_sql = """INSERT OR REPLACE INTO analytics_players
        (match_id, slot, house_id, house_name, team, controller, qbot_difficulty, result,
         final_credits, spice_harvested, units_built, structures_built, units_destroyed,
         structures_destroyed, units_lost, structures_lost, military_value, city_population, city_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    unit_sql = """INSERT OR REPLACE INTO analytics_qbot_units
        (match_id, slot, item_id, item_name, target_weight_bps, built, lost, destroyed,
         reward_milli, lost_value, damage_value_milli, kill_bonus_milli)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    for default_slot, player in enumerate(players[:MAX_PLAYERS]):
        if not isinstance(player, dict):
            continue
        slot = integer(player.get("slot", default_slot), 0, MAX_PLAYERS - 1)
        if slot is None:
            continue
        controller = text(player.get("controller", "unknown"), 32)
        connection.execute(player_sql, (
            match_id, slot, integer(player.get("house_id"), 0, 255), text(player.get("house_name")),
            integer(player.get("team"), -1, 255), controller,
            text(player.get("qbot_difficulty"), 32), text(player.get("result"), 16),
            integer(player.get("final_credits")), integer(player.get("spice_harvested"), 0),
            integer(player.get("units_built"), 0), integer(player.get("structures_built"), 0),
            integer(player.get("units_destroyed"), 0), integer(player.get("structures_destroyed"), 0),
            integer(player.get("units_lost"), 0), integer(player.get("structures_lost"), 0),
            integer(player.get("military_value"), 0), integer(player.get("city_population"), 0),
            integer(player.get("city_value"), 0),
        ))
        units = player.get("qbot_units")
        if controller != "qbot" or not isinstance(units, list):
            continue
        for unit in units[:MAX_UNIT_ROWS]:
            if not isinstance(unit, dict):
                continue
            item_id = integer(unit.get("item_id"), 0, 10000)
            if item_id is None:
                continue
            connection.execute(unit_sql, (
                match_id, slot, item_id, text(unit.get("item_name")),
                integer(unit.get("target_weight_bps"), 0, 100000), integer(unit.get("built"), 0),
                integer(unit.get("lost"), 0), integer(unit.get("destroyed"), 0),
                integer(unit.get("reward_milli"), 0), integer(unit.get("lost_value"), 0),
                integer(unit.get("damage_value_milli"), 0), integer(unit.get("kill_bonus_milli"), 0),
            ))


def record(connection: sqlite3.Connection, request: dict[str, Any]) -> None:
    phase = request.get("phase")
    match_id = text(request.get("match_id"), 96)
    payload = request.get("payload")
    source = text(request.get("source", "v1"), 32) or "v1"
    if phase not in {"start", "end"} or not match_id or not isinstance(payload, dict):
        raise ValueError("invalid record request")
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds storage limit")
    data = fields(payload)
    now = int(time.time())
    with connection:
        if phase == "start":
            connection.execute("""INSERT INTO analytics_matches
                (match_id, schema_version, source, started_at, updated_at, outcome, game_type, map_name,
                 map_width, map_height, map_seed, mod_name, mod_version, game_version, player_count,
                 human_count, qbot_count, start_json)
                VALUES (?, ?, ?, ?, ?, 'started', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET schema_version=excluded.schema_version,
                 source=excluded.source, updated_at=excluded.updated_at, game_type=excluded.game_type,
                 map_name=excluded.map_name, map_width=excluded.map_width, map_height=excluded.map_height,
                 map_seed=excluded.map_seed, mod_name=excluded.mod_name, mod_version=excluded.mod_version,
                 game_version=excluded.game_version, player_count=excluded.player_count,
                 human_count=excluded.human_count, qbot_count=excluded.qbot_count, start_json=excluded.start_json""",
                (match_id, data["schema_version"], source, now, now, data["game_type"], data["map_name"],
                 data["map_width"], data["map_height"], data["map_seed"], data["mod_name"],
                 data["mod_version"], data["game_version"], data["player_count"], data["human_count"],
                 data["qbot_count"], encoded))
        else:
            connection.execute("""INSERT INTO analytics_matches
                (match_id, schema_version, source, started_at, ended_at, updated_at, outcome, game_type, map_name,
                 map_width, map_height, map_seed, mod_name, mod_version, game_version, player_count, human_count,
                 qbot_count, duration_cycles, duration_seconds, winning_house, total_spice_harvested,
                 total_units_destroyed, total_structures_destroyed, end_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET ended_at=excluded.ended_at, updated_at=excluded.updated_at,
                 outcome=excluded.outcome, duration_cycles=excluded.duration_cycles,
                 duration_seconds=excluded.duration_seconds, winning_house=excluded.winning_house,
                 total_spice_harvested=excluded.total_spice_harvested,
                 total_units_destroyed=excluded.total_units_destroyed,
                 total_structures_destroyed=excluded.total_structures_destroyed, end_json=excluded.end_json""",
                (match_id, data["schema_version"], source, now, now, now, data["outcome"], data["game_type"],
                 data["map_name"], data["map_width"], data["map_height"], data["map_seed"], data["mod_name"],
                 data["mod_version"], data["game_version"], data["player_count"], data["human_count"],
                 data["qbot_count"], data["duration_cycles"], data["duration_seconds"], data["winning_house"],
                 data["total_spice_harvested"], data["total_units_destroyed"],
                 data["total_structures_destroyed"], encoded))
        store_players(connection, match_id, payload)


def summary(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute("""SELECT COUNT(*) AS started,
        SUM(CASE WHEN ended_at IS NOT NULL THEN 1 ELSE 0 END) AS finished,
        SUM(CASE WHEN started_at >= strftime('%s', 'now', '-30 days') THEN 1 ELSE 0 END) AS last_30_days
        FROM analytics_matches""").fetchone()
    return {"started": int(row[0] or 0), "finished": int(row[1] or 0), "last_30_days": int(row[2] or 0)}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        database_path = request.get("database")
        if not isinstance(database_path, str) or not database_path:
            raise ValueError("database path is required")
        connection = open_database(database_path)
        try:
            action = request.get("action")
            if action == "record":
                record(connection, request)
                response: dict[str, Any] = {"ok": True}
            elif action in {"health", "summary"}:
                response = {"ok": True, **summary(connection)}
            else:
                raise ValueError("unknown action")
        finally:
            connection.close()
        print(json.dumps(response, separators=(",", ":")))
        return 0
    except Exception as error:  # concise server-side diagnostic; never echo payload
        print(f"analytics helper failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
