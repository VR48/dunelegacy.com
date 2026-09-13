<?php
declare(strict_types=1);
require __DIR__ . '/../../metaserver/p2p_notifications.php';
function check(bool $ok, string $what): void { if (!$ok) throw new RuntimeException($what); }
$root = sys_get_temp_dir() . '/p2p-notify-' . bin2hex(random_bytes(8));
mkdir($root, 0700);
$now = 1000;
$sent = [];
$replies = [];
$url = 'https://discord.com/api/webhooks/123/test-token';
$transport = static function ($payload) use (&$sent, &$replies): array {
    $sent[] = $payload;
    return array_shift($replies) ?? ['status' => 200, 'message_id' => '123456'];
};
$clock = static function () use (&$now): int { return $now; };
$event = ['room_log_id' => str_repeat('a', 32), 'mode' => 'custom', 'visibility' => 'private',
    'host' => '@everyone', 'version' => '1.0.682', 'players' => 1, 'max_players' => 4,
    'code' => 'PRIVATE-SECRET', 'session' => str_repeat('b', 64)];
try {
    $notifier = new P2PNotifications($root . '/queue', $url, $transport, $clock);
    $notifier->enqueue('hosted', $event);
    $notifier->enqueue('hosted', $event);
    $notifier->drain();
    $notifier->drain();
    check(count($sent) === 1, 'Host retries must not duplicate a delivered message');
    check($sent[0]['allowed_mentions']['parse'] === [], 'Player text must not ping Discord users');
    $json = json_encode($sent[0]);
    check(!str_contains($json, 'PRIVATE-SECRET') && !str_contains($json, str_repeat('b', 64)), 'No private credentials');
    check(str_contains($json, 'Custom Game Lobby Created') && str_contains($json, 'Private'), 'Private custom lobby content');

    $event['players'] = 2;
    $notifier->enqueue('started', $event);
    $replies[] = ['status' => 429, 'retry_after' => 12];
    $now += 5;
    $notifier->drain();
    check(count($sent) === 2, 'Start attempted');
    $now += 11;
    $notifier->drain();
    check(count($sent) === 2, 'Retry-after respected');
    $now++;
    $notifier->drain();
    check(count($sent) === 3, 'Rate-limited start retried');
    $now += 60;
    $notifier->enqueue('started', $event);
    $notifier->drain();
    check(count($sent) === 3, 'Repeated start remains deduplicated');

    foreach (['custom', 'coop'] as $mode) foreach (['public', 'private'] as $visibility) {
        $event['mode'] = $mode; $event['visibility'] = $visibility;
        $payload = P2PNotifications::payload('hosted', $event);
        check($payload !== null, 'Both lobby modes and visibilities supported');
        check(str_contains(json_encode($payload), $mode === 'coop' ? 'Campaign Co-op' : 'Custom Game'), 'Correct mode label');
    }
    $event['room_log_id'] = str_repeat('c', 32);
    $notifier->enqueue('hosted', $event);
    $replies[] = ['status' => 503];
    $notifier->drain();
    $now += 30;
    $notifier->drain();
    check(count($sent) === 5, 'Transient server failure retried');

    $event['room_log_id'] = str_repeat('d', 32);
    $notifier->enqueue('hosted', $event);
    $now += 5;
    $replies[] = ['status' => 404];
    $notifier->drain();
    $now += 60;
    $notifier->drain();
    check(count($sent) === 6, 'Invalid webhook does not retry indefinitely');

    $invalid = new P2PNotifications($root . '/invalid', 'http://localhost/private', $transport, $clock);
    $invalid->enqueue('hosted', $event); $invalid->drain();
    check(!is_dir($root . '/invalid'), 'Only configured Discord HTTPS endpoints permitted');
    check(P2PNotifications::payload('joined', $event) === null, 'No per-join spam');
    $event['players'] = 99;
    check(P2PNotifications::payload('hosted', $event) === null, 'Reject impossible player counts');
    echo "P2P Discord payload, deduplication, privacy, pacing and retry checks passed\n";
} finally {
    foreach (glob($root . '/*/*') as $file) unlink($file);
    foreach (glob($root . '/*') as $dir) rmdir($dir);
    rmdir($root);
}
