<?php
/** Authenticated relay lifecycle storage. Never accepts player credentials or packets. */
require_once __DIR__ . '/analytics.php';
const RELAY_ANALYTICS_MAX_BYTES = 4096;

function relayAnalyticsAuthenticate($raw, $timestamp, $signature, $key, $now) {
    if (!is_string($key) || strlen($key) < 32 || !is_string($raw)
        || strlen($raw) > RELAY_ANALYTICS_MAX_BYTES || !is_string($timestamp)
        || !preg_match('/\A[0-9]{10}\z/D', $timestamp)
        || abs($now - (int)$timestamp) > 300 || !is_string($signature)
        || !preg_match('/\A[0-9a-f]{64}\z/D', $signature)) return false;
    return hash_equals(hash_hmac('sha256', $timestamp . "\n" . $raw, $key), $signature);
}

function relayAnalyticsEvent($raw) {
    if (!is_string($raw) || strlen($raw) > RELAY_ANALYTICS_MAX_BYTES) return null;
    $event = json_decode($raw, true, 8);
    if (!is_array($event) || ($event['schema_version'] ?? null) !== 1) return null;
    // Reject extra fields so tickets, room invitation codes and packet bodies cannot be stored.
    $keys = ['schema_version','event_id','room_id','kind','occurred_at','participant_id',
             'client_runtime','game_version','reason'];
    if (array_diff(array_keys($event), $keys) || array_diff($keys, array_keys($event))) return null;
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
    return $event;
}

class RelayAnalyticsConflict extends RuntimeException {}

function relayAnalyticsRecord(array $event) {
    // Keep internal callers subject to exactly the same validation as HTTP callers.
    if (relayAnalyticsEvent(json_encode($event)) === null) return false;
    $database = analyticsDatabase();
    if ($database === null) {
        $result = analyticsPythonRequest('relay_record', ['event' => $event]);
        if ($result !== null && ($result['status'] ?? '') === 'conflict') throw new RelayAnalyticsConflict();
        return $result !== null;
    }
    try {
        $database->exec(file_get_contents(__DIR__ . '/relay_analytics.sql'));
        $statement = $database->prepare('INSERT INTO analytics_relay_events
            (event_id, room_id, kind, occurred_at, received_at, participant_id, client_runtime, game_version, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING');
        $statement->execute([$event['event_id'], $event['room_id'], $event['kind'], $event['occurred_at'], time(),
                             $event['participant_id'], $event['client_runtime'], $event['game_version'], $event['reason']]);
        if ($statement->rowCount() === 0) {
            $lookup = $database->prepare('SELECT room_id,kind,occurred_at,participant_id,client_runtime,game_version,reason
                FROM analytics_relay_events WHERE event_id=?');
            $lookup->execute([$event['event_id']]);
            $expected = [$event['room_id'],$event['kind'],$event['occurred_at'],$event['participant_id'],
                         $event['client_runtime'],$event['game_version'],$event['reason']];
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
