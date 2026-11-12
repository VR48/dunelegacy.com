<?php
// Staging counterpart of the reviewed immutable-preview wrapper; never uses production state.
declare(strict_types=1);
if (basename(dirname(__DIR__)) !== 'play-staging') {
    http_response_code(503);
    return;
}
$stagingRoot = '/var/www/data/dunecity-p2p-staging';
define('DATA_DIR', $stagingRoot . '/run');
require_once dirname(__DIR__, 2) . '/metaserver/relay_analytics.php';

// Keep the existing lifecycle receiver/schema, with a separate staging SQLite database.
// No Discord/public-activity hooks are installed.
function dunecityP2PRecordEvent(array $event): void {
    $participant = in_array($event['kind'] ?? '', ['joined', 'left'], true);
    $record = [
        'schema_version' => 3,
        'event_id' => $event['event_id'] ?? '',
        'room_id' => $event['room_log_id'] ?? '',
        'kind' => $event['kind'] ?? '',
        'occurred_at' => $event['occurred_at'] ?? 0,
        'participant_id' => $participant ? ($event['participant_id'] ?? 0) : 0,
        'client_runtime' => $participant ? ($event['runtime_claimed'] ?? 'unknown') : 'unknown',
        'game_version' => $participant ? ($event['game_version'] ?? '') : '',
        'reason' => $event['reason'] ?? '',
        'transport' => 'direct-p2p',
    ];
    if (!relayAnalyticsRecord($record)) error_log('Staging lifecycle storage unavailable');
}
putenv('DUNECITY_P2P_CONFIG=' . $stagingRoot . '/config.php');
require $stagingRoot . '/current/public/index.php';
