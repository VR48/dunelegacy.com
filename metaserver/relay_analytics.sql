-- Additive: existing native/server-list and match-summary tables are untouched.
CREATE TABLE IF NOT EXISTS analytics_relay_events (
    event_id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('created','joined','started','left','closed')),
    occurred_at INTEGER NOT NULL,
    received_at INTEGER NOT NULL,
    participant_id INTEGER NOT NULL,
    client_runtime TEXT NOT NULL CHECK(client_runtime IN ('browser','native','unknown')),
    game_version TEXT NOT NULL,
    reason TEXT NOT NULL,
    transport TEXT NOT NULL DEFAULT 'wss' CHECK(transport = 'wss'),
    source TEXT NOT NULL DEFAULT 'relay_service_v1' CHECK(source = 'relay_service_v1')
);
CREATE INDEX IF NOT EXISTS analytics_relay_room_idx
    ON analytics_relay_events(room_id, occurred_at);
CREATE VIEW IF NOT EXISTS analytics_relay_participants AS
SELECT room_id, participant_id,
       COALESCE(MAX(CASE WHEN kind='joined' THEN client_runtime END), 'unknown') AS client_runtime,
       MAX(CASE WHEN kind='joined' THEN game_version END) AS game_version,
       MIN(CASE WHEN kind='joined' THEN occurred_at END) AS joined_at,
       MAX(CASE WHEN kind='left' THEN occurred_at END) AS left_at,
       'wss' AS transport,
       'client_reported' AS runtime_source
FROM analytics_relay_events WHERE participant_id > 0
GROUP BY room_id, participant_id;
