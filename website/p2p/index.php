<?php
// The service source and state remain outside the public website deployment tree.
// This tracked entrypoint survives scheduled website deployments using rsync --delete.
declare(strict_types=1);
if (!defined('DATA_DIR')) define('DATA_DIR', '/var/www/data');
require_once dirname(__DIR__) . '/metaserver/relay_analytics.php';

/** Only the installed signaling service calls this hook; there is no HTTP event input here. */
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
    if (!relayAnalyticsRecord($record)) error_log('P2P lifecycle storage unavailable');
}

putenv('DUNECITY_P2P_CONFIG=/var/www/data/dunecity-p2p/config.php');
require '/var/www/data/dunecity-p2p/current/public/index.php';
