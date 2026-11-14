<?php
require_once __DIR__ . '/donations_service.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
// Same-site page only. No wildcard CORS.
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if($origin !== '' && $origin !== 'https://dunelegacy.com' && $origin !== 'https://www.dunelegacy.com') {
    http_response_code(403); exit('{"error":"Origin is not allowed."}');
}
$method = $_SERVER['REQUEST_METHOD'] ?? '';
$action = is_string($_GET['action'] ?? null) ? $_GET['action'] : '';
$posts = ['crypto' => 'donationCrypto', 'paypal-start' => 'donationPaypalStart', 'paypal-status' => 'donationPaypalStatus'];
$ipn = $method === 'POST' && $action === 'paypal-ipn';
if(!($method === 'GET' && $action === 'supporters') && !($method === 'POST' && ($ipn || isset($posts[$action]))
    && (int)($_SERVER['CONTENT_LENGTH'] ?? 0) <= 16384)) {
    http_response_code(400); exit('{"error":"Invalid request."}');
}
try {
    $dataDir = getenv('DATA_DIR') ?: '/var/www/data';
    // Credentials are provisioned outside the published web root.
    $io = donationIo($dataDir);
    $db = donationDatabase($dataDir . '/donations.sqlite');
    if($method === 'GET') $result = donationSupporters($db, $io);
    // PayPal's server notification: validated by posting it back to PayPal, so no rate limit.
    elseif($ipn) { http_response_code(donationPaypalIpn(file_get_contents('php://input'), $_POST, $db, $io) ? 200 : 503); exit; }
    // Status polls only read the database, so they do not use up payment attempts.
    elseif($action === 'paypal-status') $result = donationPaypalStatus($_POST, $db, $io);
    elseif(!donationAdmit($db, $_SERVER['REMOTE_ADDR'] ?? '', $io['key'], $io['now']))
        $result = ['error' => 'Too many attempts. Please try again in an hour.'];
    else $result = $posts[$action]($_POST, $db, $io);
    echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
} catch(Throwable $error) {
    // Never return exception strings, credentials or upstream bodies. Verified payments are kept.
    http_response_code(503);
    echo '{"error":"Donations are temporarily unavailable. Nothing is lost; please try again in a few minutes."}';
}
