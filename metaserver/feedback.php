<?php
require_once __DIR__ . '/feedback_service.php';
header('Content-Type: text/plain; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
// Only the official Play Online origins may read this write endpoint. No wildcard CORS.
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if($origin !== '' && $origin !== 'https://dunelegacy.com' && $origin !== 'https://www.dunelegacy.com') {
    http_response_code(403); exit('ERROR Feedback origin is not allowed.');
}
if($origin !== '') { header('Access-Control-Allow-Origin: ' . $origin); header('Vary: Origin'); }
if((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 65536 || ($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    http_response_code(400); exit('ERROR Invalid feedback request.');
}
try {
    $dataDir = getenv('DATA_DIR') ?: '/var/www/data';
    // Provision separately from code; the file is outside the published web root.
    $tokenFile = $dataDir . '/feedback_github_token.txt';
    $token = is_readable($tokenFile) ? trim(file_get_contents($tokenFile)) : '';
    if($token === '') exit('ERROR Feedback service is not configured yet. Please try again later.');
    $db = feedbackDatabase($dataDir . '/feedback.sqlite');
    echo feedbackHandle('POST', $_POST, $_SERVER['REMOTE_ADDR'] ?? '', $token, $db, 'feedbackGithub');
} catch(Throwable $error) {
    // Never return exception strings, credentials or upstream response bodies to a player.
    http_response_code(503);
    echo 'ERROR Feedback service is unavailable. Your text is still here; try again later.';
}
