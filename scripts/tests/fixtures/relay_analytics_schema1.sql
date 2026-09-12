-- Frozen copy of the deployed schema-1 relay lifecycle table, view and index.
--
-- Tests seed a database with exactly this, then let the receiver migrate it, so the schema-2
-- migration is exercised against the constraints that are actually in production
-- (CHECK(transport = 'wss') and a view that reports the literal 'wss') rather than against
-- whatever the current relay_analytics.sql happens to say. Do not update this file when the
-- live schema changes.
CREATE TABLE IF NOT EXISTS analytics_relay_events (
    event_id TEXT PRIMARY KEY NOT NULL CHECK(length(event_id) BETWEEN 22 AND 64 AND event_id NOT GLOB '*[^A-Za-z0-9_-]*'),
    room_id TEXT NOT NULL CHECK(length(room_id) BETWEEN 22 AND 64 AND room_id NOT GLOB '*[^A-Za-z0-9_-]*'),
    kind TEXT NOT NULL CHECK(kind IN ('created','joined','started','left','closed')),
    occurred_at INTEGER NOT NULL CHECK(typeof(occurred_at)='integer' AND occurred_at BETWEEN 0 AND 4102444800),
    received_at INTEGER NOT NULL CHECK(typeof(received_at)='integer' AND received_at BETWEEN 0 AND 4102444800),
    participant_id INTEGER NOT NULL CHECK(typeof(participant_id)='integer' AND participant_id BETWEEN 0 AND 4294967295),
    client_runtime TEXT NOT NULL CHECK(client_runtime IN ('browser','native','unknown')),
    game_version TEXT NOT NULL CHECK(length(game_version)<=64 AND game_version NOT GLOB '*[^A-Za-z0-9._-]*'),
    reason TEXT NOT NULL CHECK(length(reason)<=48 AND reason NOT GLOB '*[^a-z0-9_-]*'),
    transport TEXT NOT NULL DEFAULT 'wss' CHECK(transport = 'wss'),
    source TEXT NOT NULL DEFAULT 'relay_service_v1' CHECK(source = 'relay_service_v1'),
    CHECK((kind IN ('joined','left') AND participant_id>0) OR
          (kind IN ('created','started','closed') AND participant_id=0 AND client_runtime='unknown' AND game_version=''))
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
