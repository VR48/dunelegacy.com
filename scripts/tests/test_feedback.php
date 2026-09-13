<?php
require_once __DIR__ . '/../../metaserver/feedback_service.php';
function check($value, $message) { if(!$value) throw new RuntimeException($message); }
$db = feedbackDatabase(':memory:');
$input = ['request_id' => str_repeat('a', 32), 'title' => 'Café & issue', 'details' => "Something broke\n#1", 'context' => 'Atreides: QuantBot Brutal'];
$calls = [];
$github = function($method, $path, $payload, $token) use (&$calls) {
    $calls[] = [$method, $path, $payload];
    check($token === 'test-repo-scoped-token', 'token not supplied server-side');
    return [201, ['html_url' => 'https://github.com/VR48/dunecity/issues/123']];
};
$send = function($data, $token = 'test-repo-scoped-token', $ip = '127.0.0.1') use ($db, $github) {
    return feedbackHandle('POST', $data, $ip, $token, $db, $github);
};
check(str_starts_with($send($input, ''), 'ERROR'), 'unconfigured service accepted feedback');
check(count($calls) === 0, 'unconfigured service made an upstream call');
check($send($input) === 'OK https://github.com/VR48/dunecity/issues/123', 'creation failed');
check(str_contains($calls[0][2]['body'], 'Atreides: QuantBot Brutal'), 'AI context missing');
check(str_contains($calls[0][2]['body'], 'dunecity-feedback:' . $input['request_id']), 'reconciliation marker missing');
check($send($input) === 'OK https://github.com/VR48/dunecity/issues/123' && count($calls) === 1, 'retry duplicated issue');
$changed = $input; $changed['details'] = 'Different';
check(str_starts_with($send($changed), 'ERROR') && count($calls) === 1, 'id accepted different content');
foreach(['title' => [], 'details' => str_repeat('x', 8001), 'request_id' => '../../bad'] as $key => $value) {
    $bad = $input; $bad[$key] = $value;
    check(str_starts_with($send($bad), 'ERROR'), 'invalid request accepted');
}
$unicode = $input; $unicode['request_id'] = str_repeat('b', 32); $unicode['details'] = str_repeat('😀', 2000);
check(str_starts_with($send($unicode), 'OK'), 'valid 2000-character UTF-8 body rejected');
$unicode['details'] .= 'x'; check(str_starts_with($send($unicode), 'ERROR'), 'character cap ignored');
check(feedbackIssueUrl(['html_url' => 'https://evil.test/123']) === '', 'foreign issue link accepted');
// A lost POST response must be reconciled, never posted twice.
$pending = $input; $pending['request_id'] = str_repeat('c', 32);
$lostCalls = 0;
$lost = function() use (&$lostCalls) { ++$lostCalls; return [0, []]; };
check(str_starts_with(feedbackHandle('POST', $pending, '::1', 'test-repo-scoped-token', $db, $lost), 'ERROR'), 'lost response reported success');
$db->exec('UPDATE feedback_requests SET created=created-30 WHERE id="' . $pending['request_id'] . '"');
$recover = function($method) use ($pending) {
    check($method === 'GET', 'uncertain request was posted again');
    return [200, [['body' => '<!-- dunecity-feedback:' . $pending['request_id'] . ' -->', 'html_url' => 'https://github.com/VR48/dunecity/issues/456']]];
};
check(feedbackHandle('POST', $pending, '::1', 'test-repo-scoped-token', $db, $recover) === 'OK https://github.com/VR48/dunecity/issues/456', 'reconciliation failed');
// Explicit rejection is retryable; reservation still prevents concurrent duplicate POSTs.
$rejected = $input; $rejected['request_id'] = str_repeat('d', 32);
feedbackHandle('POST', $rejected, 'reject-ip', 'test-repo-scoped-token', $db, fn() => [403, []]);
$retry = function($method) use ($db, $rejected) {
    check($db->query('SELECT state FROM feedback_requests WHERE id="' . $rejected['request_id'] . '"')->fetchColumn() === 'pending', 'retry not reserved');
    return [201, ['html_url' => 'https://github.com/VR48/dunecity/issues/789']];
};
check(str_starts_with(feedbackHandle('POST', $rejected, 'reject-ip', 'test-repo-scoped-token', $db, $retry), 'OK'), 'rejection could not be retried');
for($i=0; $i<7; ++$i) {
    $limited = $input; $limited['request_id'] = sprintf('%032x', $i + 100);
    $result = $send($limited, 'test-repo-scoped-token', 'rate-ip');
}
check(str_contains($result, 'limit reached'), 'rate limit missing');
check(!str_contains($db->query('SELECT ip FROM feedback_attempts LIMIT 1')->fetchColumn(), '127.'), 'stored raw IP');
echo "Feedback service tests passed (mock GitHub; no public issues created).\n";
