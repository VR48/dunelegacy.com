<?php
// Copied unchanged into the committed preview's p2p/index.php. No public control route.
declare(strict_types=1);
$name = basename(dirname(__DIR__));
if (!preg_match('/^play-test-[a-z0-9-]{1,80}$/D', $name)) {
    http_response_code(503);
    return;
}
$previewRoot = '/var/www/data/dunecity-p2p-previews/' . $name;
define('DATA_DIR', $previewRoot . '/run');
require_once dirname(__DIR__, 2) . '/metaserver/relay_analytics.php';

// Same trusted lifecycle receiver/schema as production, with a separate preview SQLite database.
// No Discord/public-activity hooks are installed; gate lobbies cannot announce production games.
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
    if (!relayAnalyticsRecord($record)) error_log('Preview lifecycle storage unavailable');
}
putenv('DUNECITY_P2P_CONFIG=' . $previewRoot . '/config.php');
require $previewRoot . '/current/public/index.php';
