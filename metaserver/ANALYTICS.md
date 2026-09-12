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

## Relay participants (additive, not deployed)

`relay-events.php` is a separate service endpoint. Existing game clients and
server-list responses are unchanged. The relay does not mount or open SQLite.
The service records authenticated lifecycle events in `analytics_relay_events`;
`analytics_relay_participants` is a queryable view over join/leave events. A
mixed room has separate native and browser participant rows. The legacy
`analytics_matches.client_runtime` remains the reporting client's metadata.
A room's lifetime is not a completed match and must not be counted as one.

The relay sends a POST with exactly these fields (maximum 4096 bytes):

```json
{
  "schema_version": 1,
  "event_id": "event-6e6a0fc80a754ff0a111741f1cf77794",
  "room_id": "room-6474d59e947d473881de1e5766d24a9d",
  "kind": "joined",
  "occurred_at": 1789190000,
  "participant_id": 1,
  "client_runtime": "browser",
  "game_version": "1.0.655",
  "reason": ""
}
```

`event_id` is a unique service-generated id reused on delivery retries.
`room_id` is an internal service-generated identifier, **never the invitation
code or admission ticket**. IDs contain 22–64 ASCII letters/digits/underscores/
hyphens. Kinds are `created`, `joined`, `started`, `left`, `closed`. Joined/left
require a positive service-assigned participant ID; other events use ID zero,
runtime `unknown`, and an empty game version. Participant runtime is `browser`,
`native` or `unknown` and remains explicitly **client reported**. Versions are
at most 64 ASCII letters/digits/dots/underscores/hyphens. Reasons are at most 48
lowercase letters/digits/underscores/hyphens and should be fixed reason codes.
Extra fields are rejected, preventing accidental storage of packet bodies,
chat or credentials. `transport=wss` and `source=relay_service_v1` are assigned
by this trusted-service endpoint, not accepted from a game client's payload.
This reflects the authenticated relay's observation; SQLite cannot verify a
player's operating system or the relay's external TLS termination itself.

Authentication uses a dedicated random server-only `DUNE_RELAY_ANALYTICS_KEY`
(minimum 32 bytes of key text) configured in both services. Do not put it in
WASM, JavaScript served to players, source control, query strings or logs.
The relay signs the **exact UTF-8 body bytes**:

```text
X-Dune-Relay-Timestamp: <current 10-digit Unix seconds>
X-Dune-Relay-Signature: lowercase_hex(HMAC-SHA256(key, timestamp + "\n" + body))
```

The endpoint fails closed when unconfigured. It checks the signature in
constant time and accepts timestamps within five minutes. Replayed valid
requests cannot duplicate or rewrite a row because `event_id` is a primary
key and inserts never update existing events. An identical retry returns 200;
reuse of an ID with different immutable event fields returns 409. Delivery retries use the same
event body/id with a fresh request timestamp/signature. Out-of-order leave and
join delivery remains queryable. Keep the relay's outbound queue bounded,
retry only transient failures, and report aggregate delivery failures without
logging credentials. Event delivery must not block the game loop.

Before deployment, restrict this route to the relay service at the proxy/
firewall, require HTTPS and normal certificate validation for a remote relay,
apply a small request/rate limit, and configure a retention job for old events
(e.g. a chosen 90-day retention). Loopback HTTP is suitable only for local tests
or an explicitly secured same-host internal hop. Keep analytics service
credentials separate from deployment/SSH credentials. This change creates no
keys, server configuration, deployment, scheduled retention or public service.

```sql
SELECT room_id,
       SUM(client_runtime = 'browser') AS browser_players,
       SUM(client_runtime = 'native') AS native_players
FROM analytics_relay_participants
GROUP BY room_id
HAVING browser_players > 0 AND native_players > 0;
```

Checks use temporary databases: `python3 -m unittest discover -s scripts/tests
-p 'test_*analytics*.py'`, `php scripts/tests/test_relay_analytics.php`, and
`php scripts/tests/test_relay_analytics.php --python`. The suite exercises the
real HTTP boundary, both SQLite adapters, authentication failures, size/field
limits, mixed rooms, duplicate/out-of-order events, and legacy record isolation.
