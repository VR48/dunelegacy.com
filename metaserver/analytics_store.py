#!/usr/bin/env python3
"""Rootless SQLite backend for metaserver match summaries.

PHP PDO remains the preferred backend.  Existing droplets created before match
analytics may not have pdo_sqlite installed, while Python's standard sqlite3
module is already present for deployment tooling.  This helper accepts one
bounded JSON request on stdin and never emits or stores client log streams.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


MAX_PLAYERS = 12
MAX_UNIT_ROWS = 48
MAX_ITEM_ROWS = 96
MAX_PAYLOAD_BYTES = 64 * 1024


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
    client_runtime TEXT NOT NULL DEFAULT 'unknown',
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
    player_id INTEGER,
    player_name TEXT,
    house_slot INTEGER,
    house_id INTEGER,
    house_name TEXT,
    team INTEGER,
    controller TEXT,
    player_class TEXT,
    ai_type TEXT,
    ai_difficulty TEXT,
    ai_support INTEGER,
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
CREATE TABLE IF NOT EXISTS analytics_player_items (
    match_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    item_name TEXT,
    item_kind TEXT,
    produced INTEGER,
    killed INTEGER,
    lost INTEGER,
    PRIMARY KEY (match_id, slot, item_id),
    FOREIGN KEY (match_id, slot) REFERENCES analytics_players(match_id, slot) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS analytics_matches_started_idx ON analytics_matches(started_at);
CREATE INDEX IF NOT EXISTS analytics_matches_mode_idx ON analytics_matches(game_type, mod_name);
CREATE INDEX IF NOT EXISTS analytics_qbot_units_item_idx ON analytics_qbot_units(item_id);
CREATE INDEX IF NOT EXISTS analytics_player_items_item_idx ON analytics_player_items(item_id);
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
    players = payload.get("players")
    players = players[:MAX_PLAYERS] if isinstance(players, list) else []
    human_count = sum(isinstance(player, dict) and player.get("controller") == "human" for player in players)
    qbot_count = sum(
        isinstance(player, dict)
        and (player.get("controller") == "qbot" or player.get("ai_type") == "qbot")
        for player in players
    )
    outcome = text(payload.get("outcome", summary.get("outcome")), 16)
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
        "client_runtime": payload.get("client_runtime") if payload.get("client_runtime") in ("browser", "native") else None,
        "player_count": integer(summary.get("player_count", len(players)), 0, MAX_PLAYERS),
        "human_count": integer(summary.get("human_count", human_count), 0, MAX_PLAYERS),
        "qbot_count": integer(summary.get("qbot_count", qbot_count), 0, MAX_PLAYERS),
        "outcome": outcome,
        "duration_cycles": integer(payload.get("duration_cycles", summary.get("duration_cycles")), 0),
        "duration_seconds": integer(payload.get("duration_seconds", summary.get("duration_seconds")), 0),
        "winning_house": integer(payload.get("winning_house", summary.get("winning_house")), -1, 255),
        "total_spice_harvested": integer(payload.get("total_spice_harvested", summary.get("total_spice_harvested")), 0),
        "total_units_destroyed": integer(payload.get("total_units_destroyed", summary.get("total_units_destroyed")), 0),
        "total_structures_destroyed": integer(payload.get("total_structures_destroyed", summary.get("total_structures_destroyed")), 0),
    }


def open_database(database_path: str) -> sqlite3.Connection:
    path = Path(database_path)
    os.umask(0o007)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o2770)
        if path.exists():
            path.chmod(0o660)
    except OSError:
        pass
    connection = sqlite3.connect(path, timeout=3)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=3000")
    connection.executescript(SCHEMA)
    match_columns = {row[1] for row in connection.execute("PRAGMA table_info(analytics_matches)")}
    if "client_runtime" not in match_columns:
        connection.execute("ALTER TABLE analytics_matches ADD COLUMN client_runtime TEXT NOT NULL DEFAULT 'unknown'")
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(analytics_players)")}
    for column, definition in (
        ("player_id", "INTEGER"), ("player_name", "TEXT"), ("house_slot", "INTEGER"),
        ("player_class", "TEXT"), ("ai_type", "TEXT"), ("ai_difficulty", "TEXT"),
        ("ai_support", "INTEGER")
    ):
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE analytics_players ADD COLUMN {column} {definition}")
    for related_path in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if related_path.exists():
                related_path.chmod(0o660)
        except OSError:
            pass
    return connection


def store_players(connection: sqlite3.Connection, match_id: str, payload: dict[str, Any]) -> None:
    connection.execute("DELETE FROM analytics_players WHERE match_id = ?", (match_id,))
    players = payload.get("players")
    if not isinstance(players, list):
        return
    player_sql = """INSERT OR REPLACE INTO analytics_players
        (match_id, slot, player_id, player_name, house_slot, house_id, house_name, team, controller,
         player_class, ai_type, ai_difficulty, ai_support, qbot_difficulty, result,
         final_credits, spice_harvested, units_built, structures_built, units_destroyed,
         structures_destroyed, units_lost, structures_lost, military_value, city_population, city_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    item_sql = """INSERT OR REPLACE INTO analytics_player_items
        (match_id, slot, item_id, item_name, item_kind, produced, killed, lost)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
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
        ai_type = text(player.get("ai_type"), 32)
        if ai_type is None and controller == "qbot":
            ai_type = "qbot"
        ai_difficulty = text(player.get("ai_difficulty", player.get("qbot_difficulty")), 32)
        connection.execute(player_sql, (
            match_id, slot, integer(player.get("player_id"), 0, 255), text(player.get("player_name")),
            integer(player.get("house_slot"), 0, 255), integer(player.get("house_id"), 0, 255),
            text(player.get("house_name")),
            integer(player.get("team"), -1, 255), controller, text(player.get("player_class"), 64),
            ai_type, ai_difficulty, 1 if player.get("ai_support") is True else 0,
            text(player.get("qbot_difficulty", ai_difficulty if ai_type == "qbot" else None), 32),
            text(player.get("result"), 16),
            integer(player.get("final_credits")), integer(player.get("spice_harvested"), 0),
            integer(player.get("units_built"), 0), integer(player.get("structures_built"), 0),
            integer(player.get("units_destroyed"), 0), integer(player.get("structures_destroyed"), 0),
            integer(player.get("units_lost"), 0), integer(player.get("structures_lost"), 0),
            integer(player.get("military_value"), 0), integer(player.get("city_population"), 0),
            integer(player.get("city_value"), 0),
        ))
        item_stats = player.get("item_stats")
        if isinstance(item_stats, list):
            for item in item_stats[:MAX_ITEM_ROWS]:
                if not isinstance(item, (list, dict)):
                    continue
                if isinstance(item, list):
                    values = item + [None] * (6 - len(item))
                    item_id, item_name, item_kind, produced, killed, lost = values[:6]
                else:
                    item_id, item_name, item_kind = item.get("item_id"), item.get("item_name"), item.get("item_kind")
                    produced, killed, lost = item.get("produced"), item.get("killed"), item.get("lost")
                item_id = integer(item_id, 0, 10000)
                if item_id is None:
                    continue
                connection.execute(item_sql, (
                    match_id, slot, item_id, text(item_name), text(item_kind, 16),
                    integer(produced, 0), integer(killed, 0), integer(lost, 0),
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
        # A missing/invalid field from an older event must not erase a known runtime.
        connection.execute("UPDATE analytics_matches SET client_runtime = COALESCE(?, client_runtime) WHERE match_id = ?",
                           (data["client_runtime"], match_id))
        store_players(connection, match_id, payload)


class RelayEventConflict(ValueError):
    pass


RELAY_SCHEMA_VERSIONS = (1, 2)
# Server-observed ingress values the store is willing to record.  Never a client's claim.
RELAY_TRANSPORTS = ("wss", "https-poll")
# Schema 1 has no transport field and always meant a wss relay, so it keeps that meaning.
RELAY_LEGACY_TRANSPORT = "wss"
RELAY_BASE_FIELDS = ("schema_version", "event_id", "room_id", "kind", "occurred_at",
                     "participant_id", "client_runtime", "game_version", "reason")
# Fields an event id owns for life.  A retry may repeat them, never rewrite one.
RELAY_IMMUTABLE_FIELDS = ("room_id", "kind", "occurred_at", "participant_id", "client_runtime",
                          "game_version", "reason", "transport")
RELAY_ROW_FIELDS = ("event_id", "room_id", "kind", "occurred_at", "received_at", "participant_id",
                    "client_runtime", "game_version", "reason", "transport", "source")


def relay_schema_statements() -> list[str]:
    """The schema-2 DDL, split into statements and checked for the migration markers."""
    sql = Path(__file__).with_name("relay_analytics.sql").read_text()
    if "'https-poll'" not in sql or "'server_observed'" not in sql:
        raise ValueError("relay analytics schema file is missing or not schema 2")
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in body.split(";") if statement.strip()]


def relay_migrate(connection: sqlite3.Connection) -> None:
    """Bring an existing database up to the schema-2 lifecycle table.

    SQLite cannot widen a CHECK constraint in place, so a schema-1 table
    (CHECK(transport='wss')) is rebuilt: the old table is renamed, the schema-2 table is created
    from relay_analytics.sql, every old row is copied through the new CHECKs, the row count is
    verified, and only then is the old table dropped.  All of it is one transaction, so a failure
    at any point leaves the original schema-1 table, its rows and its indexes as they were.  The
    view is rebuilt with it because it used to report the literal 'wss'.
    """
    statements = relay_schema_statements()
    table_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table'"
                                   " AND name='analytics_relay_events'").fetchone()
    view_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='view'"
                                  " AND name='analytics_relay_participants'").fetchone()
    table_stale = table_sql is not None and "https-poll" not in (table_sql[0] or "")
    view_stale = view_sql is not None and "server_observed" not in (view_sql[0] or "")
    if connection.in_transaction:
        connection.commit()
    if not table_stale and not view_stale:
        for statement in statements:
            connection.execute(statement)
        if connection.in_transaction:
            connection.commit()
        return
    columns = ", ".join(RELAY_ROW_FIELDS)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DROP VIEW IF EXISTS analytics_relay_participants")
        if table_stale:
            before = connection.execute("SELECT COUNT(*) FROM analytics_relay_events").fetchone()[0]
            # Fails rather than overwrites if an older copy is somehow still present.
            connection.execute("ALTER TABLE analytics_relay_events"
                               " RENAME TO analytics_relay_events_schema1")
            connection.execute("DROP INDEX IF EXISTS analytics_relay_room_idx")
            for statement in statements:
                connection.execute(statement)
            connection.execute(f"INSERT INTO analytics_relay_events ({columns})"
                               f" SELECT {columns} FROM analytics_relay_events_schema1")
            after = connection.execute("SELECT COUNT(*) FROM analytics_relay_events").fetchone()[0]
            if after != before:
                raise ValueError("relay analytics migration lost rows")
            connection.execute("DROP TABLE analytics_relay_events_schema1")
        else:
            for statement in statements:
                connection.execute(statement)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def relay_normalize(event: Any) -> dict[str, Any]:
    """Validate one event and return it in storage form: the nine schema-1 fields plus an
    explicit transport.  Either shape is accepted, so an already normalised record stays valid
    input.  A schema-1 record may only ever mean the legacy wss transport."""
    if (not isinstance(event, dict) or not set(RELAY_BASE_FIELDS) <= set(event)
            or not set(event) <= set(RELAY_BASE_FIELDS) | {"transport"}
            or type(event["schema_version"]) is not int
            or event["schema_version"] not in RELAY_SCHEMA_VERSIONS):
        raise ValueError("invalid relay event")

    def matches(key: str, pattern: str) -> bool:
        return isinstance(event[key], str) and re.fullmatch(pattern, event[key], flags=re.ASCII) is not None
    if (not all(matches(key, r"[a-zA-Z0-9_-]{22,64}") for key in ("event_id", "room_id"))
        or event["kind"] not in ("created", "joined", "started", "left", "closed")
        or type(event["occurred_at"]) is not int or not 0 <= event["occurred_at"] <= 4102444800
        or type(event["participant_id"]) is not int or not 0 <= event["participant_id"] <= 4294967295
        or event["client_runtime"] not in ("browser", "native", "unknown")
        or not matches("game_version", r"[a-zA-Z0-9._-]{0,64}")
        or not matches("reason", r"[a-z0-9_-]{0,48}")):
        raise ValueError("invalid relay event")
    participant = event["kind"] in ("joined", "left")
    if participant != (event["participant_id"] > 0) or (not participant and
        (event["client_runtime"] != "unknown" or event["game_version"] != "")):
        raise ValueError("invalid relay participant")
    transport = event.get("transport")
    if event["schema_version"] == 1:
        if transport is not None and transport != RELAY_LEGACY_TRANSPORT:
            raise ValueError("invalid relay transport")
        transport = RELAY_LEGACY_TRANSPORT
    elif not isinstance(transport, str) or transport not in RELAY_TRANSPORTS:
        raise ValueError("invalid relay transport")
    return {**{field: event[field] for field in RELAY_BASE_FIELDS}, "transport": transport}


def relay_record(connection: sqlite3.Connection, request: dict[str, Any]) -> None:
    """Trusted service events only. The PHP endpoint authenticates before invoking this helper."""
    event = relay_normalize(request.get("event"))
    relay_migrate(connection)
    with connection:
        inserted = connection.execute("""INSERT INTO analytics_relay_events
            (event_id,room_id,kind,occurred_at,received_at,participant_id,client_runtime,game_version,reason,transport)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
            (event["event_id"], event["room_id"], event["kind"], event["occurred_at"], int(time.time()),
             event["participant_id"], event["client_runtime"], event["game_version"], event["reason"],
             event["transport"]))
        if inserted.rowcount == 0:
            # An id already used for a different event never changes what is stored, including a
            # schema-1 retry that would otherwise relabel an https-poll room as wss.
            fields = ",".join(RELAY_IMMUTABLE_FIELDS)
            existing = connection.execute(
                f"SELECT {fields} FROM analytics_relay_events WHERE event_id=?",
                (event["event_id"],)).fetchone()
            expected = tuple(event[key] for key in RELAY_IMMUTABLE_FIELDS)
            if existing != expected:
                raise RelayEventConflict("conflicting relay event id")


def summary(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute("""SELECT COUNT(*) AS started,
        SUM(CASE WHEN ended_at IS NOT NULL THEN 1 ELSE 0 END) AS finished,
        SUM(CASE WHEN started_at >= strftime('%s', 'now', '-30 days') THEN 1 ELSE 0 END) AS last_30_days
        FROM analytics_matches""").fetchone()
    return {"started": int(row[0] or 0), "finished": int(row[1] or 0), "last_30_days": int(row[2] or 0)}


def main() -> int:
    try:
        raw_request = sys.stdin.read(MAX_PAYLOAD_BYTES + 4097)
        if len(raw_request.encode("utf-8")) > MAX_PAYLOAD_BYTES + 4096:
            raise ValueError("request exceeds storage limit")
        request = json.loads(raw_request)
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
            elif action == "relay_record":
                try:
                    relay_record(connection, request)
                    response = {"ok": True, "status": "recorded"}
                except RelayEventConflict:
                    response = {"ok": True, "status": "conflict"}
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
