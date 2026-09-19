-- Public lobby content and named public game lifecycle, separate from anonymous relay rows.
CREATE TABLE IF NOT EXISTS analytics_public_activity (
    event_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('chat_message','public_game_created','public_game_joined','public_game_started')),
    occurred_at INTEGER NOT NULL,
    received_at INTEGER NOT NULL,
    room_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'signaling_service_v1' CHECK(source='signaling_service_v1')
);
CREATE INDEX IF NOT EXISTS analytics_public_activity_room ON analytics_public_activity(room_id, occurred_at);
CREATE INDEX IF NOT EXISTS analytics_public_activity_kind ON analytics_public_activity(kind, occurred_at);
