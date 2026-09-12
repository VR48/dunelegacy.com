# Metaserver match analytics

`metaserver.php?command=gamestats` accepts one compact JSON record when a game
starts and one when it ends. Native and browser clients submit HTTP POST requests. The
server expands the records into SQLite at `$DATA_DIR/games.sqlite`; it never
accepts the local AI decision log.

The endpoint accepts:

```text
command=gamestats
phase=start|end
match_id=<96-character opaque id>
stats=<JSON object, maximum 64 KiB>
```

`command=gamestats&phase=health` opens and migrates the database without
creating a match row.

Schema version 3 uses the existing multiplayer player list as its base. The
start event contains the map, mod, version, and one row per actual player:

```json
{
  "schema_version": 3,
  "client_runtime": "browser",
  "game_type": "multiplayer",
  "game_version": "1.0.598",
  "map": { "name": "Four Corners", "width": 64, "height": 64, "seed": 42 },
  "mod": { "name": "vanilla" },
  "players": [
    {
      "slot": 0,
      "player_id": 7,
      "player_name": "Player",
      "house_slot": 0,
      "house_id": 0,
      "house_name": "Atreides",
      "team": 1,
      "controller": "human",
      "player_class": "HumanPlayer",
      "shared_house_players": 1
    }
  ]
}
```

The end event repeats the player list and adds result, credits, harvested spice,
aggregate totals, military value, and sparse per-type statistics. Each
`item_stats` row is:

```json
[31, "Harvester", "unit", 4, 2, 1]
```

The positions mean `item_id`, `item_name`, `unit|structure`, `produced`,
`killed`, and `lost`. Rows whose three counters are all zero are omitted. QBot
players also add their final allocation weights and combat score components in
`qbot_units`. Match outcome and duration appear once at the root.

The database has four tables:

- `analytics_matches`: map, mode, version, timing, and the bounded start/end JSON.
- `analytics_players`: one row per actual player, with the same display name and
  house association already sent by multiplayer `gamestart`. `player_class`
  preserves the exact controller implementation. AI rows also contain normalized
  `ai_type`, `ai_difficulty`, and `ai_support` fields. These identify QBot,
  Mentat, classic AI, Campaign AI, and SmartBot independently of optional
  algorithm-specific metrics.
- `analytics_player_items`: per-player/house production, kill and loss counts for
  every unit and building type used in the match.
- `analytics_qbot_units`: optional QBot allocation and combat performance data.
  Other AI implementations have ordinary player and per-item rows without
  fabricated allocation ratios.

Start and end are idempotent upserts. An end event can create a completed record
if its start was lost. Clients older than 1.0.598 still create a start-only row
from their existing `House: Player` multiplayer list. New clients use the
structured pair, avoiding duplicate multiplayer rows while preserving the
Discord notification and `stats.json` behaviour.

Example queries:

```sql
-- Every participant and result from completed matches.
SELECT m.started_at, m.game_type, m.map_name, p.player_name, p.house_name,
       p.controller, p.player_class, p.ai_type, p.ai_difficulty,
       p.result, p.spice_harvested
FROM analytics_players AS p
JOIN analytics_matches AS m USING (match_id)
WHERE m.ended_at IS NOT NULL
ORDER BY m.started_at DESC, p.slot;

-- Produced, killed and lost totals by type.
SELECT i.item_name, i.item_kind,
       SUM(i.produced) AS produced, SUM(i.killed) AS killed, SUM(i.lost) AS lost
FROM analytics_player_items AS i
JOIN analytics_matches AS m USING (match_id)
WHERE m.outcome = 'finished'
GROUP BY i.item_id, i.item_name, i.item_kind
ORDER BY produced DESC;

-- QBot unit mix against reward-to-loss value.
SELECT q.item_id, q.item_name,
       ROUND(AVG(q.target_weight_bps) / 100.0, 1) AS target_percent,
       SUM(q.built) AS built, SUM(q.lost) AS lost,
       ROUND(1.0 * SUM(q.reward_milli) / NULLIF(SUM(q.lost_value) * 1000, 0), 3)
           AS reward_per_loss
FROM analytics_qbot_units AS q
JOIN analytics_matches AS m USING (match_id)
WHERE m.outcome = 'finished'
GROUP BY q.item_id, q.item_name;
```

Display names are the same names already advertised when multiplayer begins.
IP addresses, chat, and local decision logs are not stored.

PHP's SQLite PDO driver (`php-sqlite3`) is preferred. Existing restricted hosts
without it use the bundled Python `sqlite3` helper with the same schema and
transactions.

## Browser versus native runtime (1.0.655+)

Schema 3 accepts the optional `client_runtime` field: `browser` or `native`.
Both PHP/PDO and the Python fallback add `analytics_matches.client_runtime`
using an additive migration with default `unknown`. Old clients, missing/invalid
values and existing rows remain compatible. Historical rows cannot reliably be
classified retroactively. Missing or invalid values on a later event do not
erase a known runtime; end-only and retried events also retain it. `source`
continues to identify the logging protocol, not the runtime. The marker is
client-reported metadata, not an authenticated identity.

Browser game start/end use a same-origin asynchronous POST queue with bounded
retries, bypassing the native SDL worker thread. The URL-encoded body avoids GET
length limits, and small requests use fetch keepalive. Closing/crashing a tab
can still lose an end event; start-only rows must not be counted as completed
games. No browser fingerprint, IP, new user identifier or local log is added.

```sql
SELECT client_runtime, COUNT(*) AS started,
       SUM(ended_at IS NOT NULL) AS ended
FROM analytics_matches
GROUP BY client_runtime;

SELECT match_id, game_version, game_type, map_name, started_at, ended_at
FROM analytics_matches
WHERE client_runtime = 'browser'
ORDER BY started_at DESC;
```

Compatibility checks: `python3 -m unittest discover -s scripts/tests -p
 'test_analytics_runtime.py'`; PHP adapter checks run with
`php scripts/tests/test_analytics_runtime.php /tmp/isolated-test/games.sqlite`.
Never point the fixture at the production database.
