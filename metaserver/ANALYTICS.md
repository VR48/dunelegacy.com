# Metaserver match analytics

`metaserver.php?command=gamestats` accepts a compact JSON match summary at the
start and end of a game. Native clients submit it as an HTTP POST, so the data
does not hit Apache's request-line limit. It writes a SQLite database at
`$DATA_DIR/games.sqlite`; it never accepts an AI decision log.

The endpoint accepts form-encoded parameters in either POST body (normal
native client path) or GET query (browser fallback):

```text
command=gamestats
phase=start|end
match_id=<96-character opaque id>
stats=<JSON object, maximum 20 KiB>
```

`command=gamestats&phase=health` opens and migrates the database without
creating a match row. Deployment uses this as a storage readiness check.

`stats` schema version 1 has this shape:

```json
{
  "schema_version": 1,
  "game_type": "campaign|skirmish|single_custom|multiplayer",
  "game_version": "1.0.583",
  "map": { "name": "Four Corners", "width": 64, "height": 64, "seed": 42 },
  "mod": { "name": "vanilla", "version": "" },
  "summary": {
    "player_count": 4,
    "human_count": 1,
    "qbot_count": 3,
    "outcome": "finished",
    "duration_cycles": 12345,
    "duration_seconds": 740,
    "winning_house": 1,
    "total_spice_harvested": 32000,
    "total_units_destroyed": 64,
    "total_structures_destroyed": 8
  },
  "players": []
}
```

Each player summary deliberately contains a house and controller class rather
than a player name. QBot players may add at most 48 `qbot_units` entries with
their production weight, built/lost/destroyed counts, reward, loss value, and
damage/kill components. Those rows live in `analytics_qbot_units`, making unit
mix and performance queries practical without retaining a large event stream.

The database has three tables:

- `analytics_matches`: one summary per opaque match ID, start and end payloads
  capped at 20 KiB each. The start omits duplicate QBot outcome counters.
- `analytics_players`: one bounded summary per active house/slot.
- `analytics_qbot_units`: bounded QBot-only unit performance data.

Both start and end are idempotent upserts. The end event can create a finished
record if a start was lost in transit. Old `gamestart` clients still work: the
server records an anonymised, start-only `legacy_gamestart` row while preserving
the existing Discord notification and `stats.json` behaviour.

The data is designed for direct SQLite interrogation. For example:

```sql
-- Activity and completion rate by mode and mod.
SELECT game_type, mod_name, COUNT(*) AS started,
       SUM(ended_at IS NOT NULL) AS finished,
       ROUND(AVG(duration_seconds), 1) AS average_seconds
FROM analytics_matches
GROUP BY game_type, mod_name
ORDER BY started DESC;

-- QBot win rate and economic outcome by difficulty.
SELECT p.qbot_difficulty, COUNT(*) AS games,
       ROUND(100.0 * AVG(p.result = 'winner'), 1) AS win_percent,
       ROUND(AVG(p.spice_harvested)) AS average_spice,
       ROUND(AVG(p.military_value)) AS average_military_value
FROM analytics_players AS p
JOIN analytics_matches AS m USING (match_id)
WHERE m.outcome = 'finished' AND p.controller = 'qbot'
GROUP BY p.qbot_difficulty;

-- Unit mix against its actual reward-to-loss value ratio. item_id 9999 is
-- QBot's combined Devastator/Sonic Tank/Deviator "special" allocation.
SELECT q.item_id, q.item_name, ROUND(AVG(q.target_weight_bps) / 100.0, 1) AS target_percent,
       SUM(q.built) AS built, SUM(q.lost) AS lost,
       ROUND(1.0 * SUM(q.reward_milli) / NULLIF(SUM(q.lost_value) * 1000, 0), 3) AS reward_per_loss
FROM analytics_qbot_units AS q
JOIN analytics_matches AS m USING (match_id)
WHERE m.outcome = 'finished'
GROUP BY q.item_id, q.item_name
ORDER BY target_percent DESC;
```

The per-house summary also includes final credits, spice, production/loss
counts, military value, and, in Dune City matches, population and average land
value. No player names, IP addresses, chat, or local decision logs enter this
database.

The production host needs PHP's SQLite PDO driver (`php-sqlite3`). The Docker
image installs it, and `deploy/create-droplet.sh` installs it for a new Ubuntu
droplet. Existing droplets need the one-time command:

```sh
sudo apt-get install php-sqlite3 && sudo systemctl restart apache2
```
