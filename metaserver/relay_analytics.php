<?php
/** Authenticated relay lifecycle storage. Never accepts player credentials or packets. */
require_once __DIR__ . '/analytics.php';
const RELAY_ANALYTICS_MAX_BYTES = 4096;
/**
 * Accepted record schemas. Schema 1 has no transport field and always meant a wss relay, so it
 * keeps that meaning forever. Schema 2 states the server-observed transport explicitly.
 */
const RELAY_ANALYTICS_SCHEMA_VERSIONS = [1, 2];
/** Server-observed ingress values the endpoint is willing to store. Never a client's claim. */
const RELAY_ANALYTICS_TRANSPORTS = ['wss', 'https-poll'];
const RELAY_ANALYTICS_LEGACY_TRANSPORT = 'wss';
const RELAY_ANALYTICS_BASE_FIELDS = ['schema_version', 'event_id', 'room_id', 'kind', 'occurred_at',
                                     'participant_id', 'client_runtime', 'game_version', 'reason'];
/** Fields an event id owns for life. A retry may repeat them; it may never rewrite one. */
const RELAY_ANALYTICS_IMMUTABLE_FIELDS = ['room_id', 'kind', 'occurred_at', 'participant_id',
                                          'client_runtime', 'game_version', 'reason', 'transport'];

/**
 * Where this deployment keeps the receiver key when the environment carries no inline key.
 * Outside DocumentRoot, on the persistent data volume, owned by the deploy account and readable
 * by www-data only (0640). The endpoint only ever reads it.
 */
const RELAY_ANALYTICS_DEFAULT_KEY_FILE = '/var/www/data/dunecity-relay/analytics.key';
const RELAY_ANALYTICS_MIN_KEY_CHARS = 32;
const RELAY_ANALYTICS_MAX_KEY_CHARS = 512;

/** The relay's own key rule: 32..512 printable ASCII characters, no spaces. */
function relayAnalyticsValidKey($key) {
    if (!is_string($key)) return null;
    $length = strlen($key);
    if ($length < RELAY_ANALYTICS_MIN_KEY_CHARS || $length > RELAY_ANALYTICS_MAX_KEY_CHARS) return null;
    return preg_match('/\A[\x21-\x7e]+\z/D', $key) ? $key : null;
}

/** Reads a key file, bounded, read-only, and refuses one any other local account could read. */
function relayAnalyticsKeyFromFile($path) {
    if (!is_string($path) || $path === '' || !is_file($path) || !is_readable($path)) return null;
    $permissions = @fileperms($path);
    if ($permissions !== false && ($permissions & 0004) !== 0) return null;
    $raw = @file_get_contents($path, false, null, 0, RELAY_ANALYTICS_MAX_KEY_CHARS + 2);
    // Trailing newline from however the file was written; the charset has no whitespace in it.
    return is_string($raw) ? relayAnalyticsValidKey(trim($raw)) : null;
}

/**
 * Resolves the receiver key, fail-closed and without ever echoing or logging it.
 *
 * 1. an inline key in the environment (unchanged behaviour, and what the test fixtures use);
 * 2. the key file an operator named explicitly;
 * 3. this deployment's fixed path, for a server whose environment carries no key at all.
 *
 * A short, malformed, missing or world-readable key resolves to null, which the endpoint
 * answers as "disabled" rather than accepting unauthenticated events.
 */
function relayAnalyticsResolveKey($inlineKey, $keyFile, $defaultKeyFile = RELAY_ANALYTICS_DEFAULT_KEY_FILE) {
    $key = relayAnalyticsValidKey($inlineKey);
    if ($key !== null) return $key;
    if (is_string($keyFile) && $keyFile !== '') return relayAnalyticsKeyFromFile($keyFile);
    return relayAnalyticsKeyFromFile($defaultKeyFile);
}

function relayAnalyticsAuthenticate($raw, $timestamp, $signature, $key, $now) {
    if (!is_string($key) || strlen($key) < 32 || !is_string($raw)
        || strlen($raw) > RELAY_ANALYTICS_MAX_BYTES || !is_string($timestamp)
        || !preg_match('/\A[0-9]{10}\z/D', $timestamp)
        || abs($now - (int)$timestamp) > 300 || !is_string($signature)
        || !preg_match('/\A[0-9a-f]{64}\z/D', $signature)) return false;
    return hash_equals(hash_hmac('sha256', $timestamp . "\n" . $raw, $key), $signature);
}

/**
 * Validates one decoded event and returns it in storage form: the nine schema-1 fields plus an
 * explicit transport. Accepts either shape, so an already normalised record stays valid input.
 * A schema-1 record may only ever mean the legacy wss transport.
 */
function relayAnalyticsNormalize($event) {
    if (!is_array($event)) return null;
    $version = $event['schema_version'] ?? null;
    if (!is_int($version) || !in_array($version, RELAY_ANALYTICS_SCHEMA_VERSIONS, true)) return null;
    foreach (RELAY_ANALYTICS_BASE_FIELDS as $field) {
        if (!array_key_exists($field, $event)) return null;
    }
    if (array_diff(array_keys($event), array_merge(RELAY_ANALYTICS_BASE_FIELDS, ['transport']))) return null;
    foreach (['event_id', 'room_id'] as $key) {
        if (!is_string($event[$key]) || !preg_match('/\A[a-zA-Z0-9_-]{22,64}\z/D', $event[$key])) return null;
    }
    if (!in_array($event['kind'], ['created','joined','started','left','closed'], true)
        || !is_int($event['occurred_at']) || $event['occurred_at'] < 0 || $event['occurred_at'] > 4102444800
        || !is_int($event['participant_id']) || $event['participant_id'] < 0 || $event['participant_id'] > 4294967295
        || !in_array($event['client_runtime'], ['browser','native','unknown'], true)
        || !is_string($event['game_version']) || !preg_match('/\A[a-zA-Z0-9._-]{0,64}\z/D', $event['game_version'])
        || !is_string($event['reason']) || !preg_match('/\A[a-z0-9_-]{0,48}\z/D', $event['reason'])) return null;
    $participant = in_array($event['kind'], ['joined','left'], true);
    if ($participant !== ($event['participant_id'] > 0)) return null;
    if (!$participant && ($event['client_runtime'] !== 'unknown' || $event['game_version'] !== '')) return null;
    // A schema-1 sender cannot name a transport, so its records keep the legacy value. A schema-2
    // sender must name one, and only the relay's own allowlisted observations are stored.
    $transport = array_key_exists('transport', $event) ? $event['transport'] : null;
    if ($version === 1) {
        if ($transport !== null && $transport !== RELAY_ANALYTICS_LEGACY_TRANSPORT) return null;
        $transport = RELAY_ANALYTICS_LEGACY_TRANSPORT;
    } elseif (!is_string($transport) || !in_array($transport, RELAY_ANALYTICS_TRANSPORTS, true)) {
        return null;
    }
    $normalized = [];
    foreach (RELAY_ANALYTICS_BASE_FIELDS as $field) $normalized[$field] = $event[$field];
    $normalized['transport'] = $transport;
    return $normalized;
}

function relayAnalyticsEvent($raw) {
    if (!is_string($raw) || strlen($raw) > RELAY_ANALYTICS_MAX_BYTES) return null;
    $event = json_decode($raw, true, 8);
    if (!is_array($event) || !is_int($event['schema_version'] ?? null)) return null;
    // Reject extra fields so tickets, room invitation codes and packet bodies cannot be stored,
    // and so a schema-1 body cannot smuggle in a transport the schema does not have.
    $keys = RELAY_ANALYTICS_BASE_FIELDS;
    if ($event['schema_version'] === 2) $keys[] = 'transport';
    if (array_diff(array_keys($event), $keys) || array_diff($keys, array_keys($event))) return null;
    return relayAnalyticsNormalize($event);
}

class RelayAnalyticsConflict extends RuntimeException {}

/** The schema-2 DDL, checked for the marker the migration below keys on. */
function relayAnalyticsSchemaSql() {
    $sql = @file_get_contents(__DIR__ . '/relay_analytics.sql');
    if (!is_string($sql) || strpos($sql, "'https-poll'") === false
        || strpos($sql, "'server_observed'") === false) {
        throw new RuntimeException('relay analytics schema file is missing or not schema 2');
    }
    return $sql;
}

/**
 * Brings an existing database up to the schema-2 lifecycle table.
 *
 * SQLite cannot widen a CHECK constraint in place, so a schema-1 table (CHECK(transport='wss'))
 * is rebuilt: the old table is renamed, the schema-2 table is created from relay_analytics.sql,
 * every old row is copied through the new CHECKs, the row count is verified, and only then is
 * the old table dropped. All of it is one transaction, so a failure at any point leaves the
 * original schema-1 table, its rows and its indexes exactly as they were. The view is rebuilt
 * with it because it used to report the literal 'wss'.
 */
function relayAnalyticsMigrate(PDO $database) {
    $schema = relayAnalyticsSchemaSql();
    $tableSql = $database->query("SELECT sql FROM sqlite_master WHERE type='table'
        AND name='analytics_relay_events'")->fetchColumn();
    $viewSql = $database->query("SELECT sql FROM sqlite_master WHERE type='view'
        AND name='analytics_relay_participants'")->fetchColumn();
    $tableStale = is_string($tableSql) && strpos($tableSql, 'https-poll') === false;
    $viewStale = is_string($viewSql) && strpos($viewSql, 'server_observed') === false;
    if (!$tableStale && !$viewStale) {
        $database->exec($schema);
        return;
    }
    $columns = 'event_id, room_id, kind, occurred_at, received_at, participant_id,
                client_runtime, game_version, reason, transport, source';
    $database->beginTransaction();
    try {
        $database->exec('DROP VIEW IF EXISTS analytics_relay_participants');
        if ($tableStale) {
            $before = (int)$database->query('SELECT COUNT(*) FROM analytics_relay_events')->fetchColumn();
            // Fails rather than overwrites if an older copy is somehow still present.
            $database->exec('ALTER TABLE analytics_relay_events RENAME TO analytics_relay_events_schema1');
            $database->exec('DROP INDEX IF EXISTS analytics_relay_room_idx');
            $database->exec($schema);
            $database->exec("INSERT INTO analytics_relay_events ($columns)
                             SELECT $columns FROM analytics_relay_events_schema1");
            $after = (int)$database->query('SELECT COUNT(*) FROM analytics_relay_events')->fetchColumn();
            if ($after !== $before) throw new RuntimeException('relay analytics migration lost rows');
            $database->exec('DROP TABLE analytics_relay_events_schema1');
        } else {
            $database->exec($schema);
        }
        $database->commit();
    } catch (Throwable $error) {
        if ($database->inTransaction()) $database->rollBack();
        throw $error;
    }
}

function relayAnalyticsRecord(array $event) {
    // Keep internal callers subject to exactly the same validation as HTTP callers.
    $event = relayAnalyticsNormalize($event);
    if ($event === null) return false;
    $database = analyticsDatabase();
    if ($database === null) {
        $result = analyticsPythonRequest('relay_record', ['event' => $event]);
        if ($result !== null && ($result['status'] ?? '') === 'conflict') throw new RelayAnalyticsConflict();
        return $result !== null;
    }
    try {
        relayAnalyticsMigrate($database);
        $statement = $database->prepare('INSERT INTO analytics_relay_events
            (event_id, room_id, kind, occurred_at, received_at, participant_id, client_runtime,
             game_version, reason, transport)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING');
        $statement->execute([$event['event_id'], $event['room_id'], $event['kind'], $event['occurred_at'], time(),
                             $event['participant_id'], $event['client_runtime'], $event['game_version'],
                             $event['reason'], $event['transport']]);
        if ($statement->rowCount() === 0) {
            // An id already used for a different event never changes what is stored, including a
            // schema-1 retry that would otherwise relabel an https-poll room as wss.
            $fields = implode(',', RELAY_ANALYTICS_IMMUTABLE_FIELDS);
            $lookup = $database->prepare("SELECT $fields FROM analytics_relay_events WHERE event_id=?");
            $lookup->execute([$event['event_id']]);
            $expected = [];
            foreach (RELAY_ANALYTICS_IMMUTABLE_FIELDS as $field) $expected[] = $event[$field];
            if ($lookup->fetch(PDO::FETCH_NUM) !== $expected) throw new RelayAnalyticsConflict();
        }
        return true;
    } catch (RelayAnalyticsConflict $error) {
        throw $error;
    } catch (Throwable $error) {
        error_log('Relay analytics storage unavailable');
        return false;
    }
}
