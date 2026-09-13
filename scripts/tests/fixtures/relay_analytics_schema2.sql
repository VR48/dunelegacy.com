-- Additive: existing native/server-list and match-summary tables are untouched.
--
-- Schema 2 of the relay lifecycle record. The only change from schema 1 is `transport`, which
-- now records which server-observed ingress the relay actually served for the event instead of
-- being pinned to the literal 'wss'. The column keeps its DEFAULT, so a schema-1 insert that
-- names no transport still means wss, and every schema-1 row already stored stays valid.
--
-- This file only ever creates a missing object. An existing schema-1 table still carries the
-- CHECK(transport = 'wss') that CREATE TABLE IF NOT EXISTS will not replace, so it is rebuilt
-- transactionally first by relayAnalyticsMigrate() in relay_analytics.php and relay_migrate()
-- in analytics_store.py. Those two look for the literal 'https-poll' in the stored table SQL
-- and 'server_observed' in the stored view SQL to decide whether a rebuild is still owed; keep
-- both markers in this file.
--
-- Both readers split this file on ';' after stripping '--' comments: no statement here may
-- contain a semicolon inside a string literal.
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
    transport TEXT NOT NULL DEFAULT 'wss' CHECK(transport IN ('wss','https-poll')),
    source TEXT NOT NULL DEFAULT 'relay_service_v1' CHECK(source = 'relay_service_v1'),
    CHECK((kind IN ('joined','left') AND participant_id>0) OR
          (kind IN ('created','started','closed') AND participant_id=0 AND client_runtime='unknown' AND game_version=''))
);
CREATE INDEX IF NOT EXISTS analytics_relay_room_idx
    ON analytics_relay_events(room_id, occurred_at);
-- transport is the relay's own observation of its ingress; client_runtime stays client reported.
-- A participant that only has a leave event falls back to that event's transport.
CREATE VIEW IF NOT EXISTS analytics_relay_participants AS
SELECT room_id, participant_id,
       COALESCE(MAX(CASE WHEN kind='joined' THEN client_runtime END), 'unknown') AS client_runtime,
       MAX(CASE WHEN kind='joined' THEN game_version END) AS game_version,
       MIN(CASE WHEN kind='joined' THEN occurred_at END) AS joined_at,
       MAX(CASE WHEN kind='left' THEN occurred_at END) AS left_at,
       COALESCE(MAX(CASE WHEN kind='joined' THEN transport END), MAX(transport)) AS transport,
       'client_reported' AS runtime_source,
       'server_observed' AS transport_source
FROM analytics_relay_events WHERE participant_id > 0
GROUP BY room_id, participant_id;
