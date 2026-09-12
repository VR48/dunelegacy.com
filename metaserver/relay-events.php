<?php
// Separate service endpoint: no changes to legacy metaserver response formats.
define('DATA_DIR', getenv('DATA_DIR') ?: __DIR__);
require_once __DIR__ . '/relay_analytics.php';
header('Content-Type: application/json');
header('Cache-Control: no-store');
function relayResponse($code, $message) {
    http_response_code($code);
    echo json_encode(['ok' => $code === 200, 'status' => $message]);
    exit;
}
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') relayResponse(405, 'method');
$key = getenv('DUNE_RELAY_ANALYTICS_KEY');
if (!is_string($key) || strlen($key) < 32) relayResponse(503, 'disabled');
$raw = file_get_contents('php://input', false, null, 0, RELAY_ANALYTICS_MAX_BYTES + 1);
if (strlen($raw) > RELAY_ANALYTICS_MAX_BYTES) relayResponse(413, 'size');
if (!relayAnalyticsAuthenticate($raw, $_SERVER['HTTP_X_DUNE_RELAY_TIMESTAMP'] ?? '',
        $_SERVER['HTTP_X_DUNE_RELAY_SIGNATURE'] ?? '', $key, time())) relayResponse(401, 'authentication');
$event = relayAnalyticsEvent($raw);
if ($event === null) relayResponse(400, 'event');
try {
    if (!relayAnalyticsRecord($event)) relayResponse(503, 'storage');
} catch (RelayAnalyticsConflict $error) {
    relayResponse(409, 'conflict');
}
relayResponse(200, 'recorded');
