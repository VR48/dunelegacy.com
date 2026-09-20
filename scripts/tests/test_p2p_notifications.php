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
    check(P2PNotifications::payload('joined', $event) === null, 'Ordinary lobby joins remain quiet');
    $named = ['room_log_id' => str_repeat('e',32), 'mode' => 'custom', 'visibility' => 'public',
        'host' => 'Codex Web 733', 'version' => 'DuneCity1.0.733', 'players' => 2, 'max_players' => 8,
        'player_names' => ['Codex Web 733', 'ggtothemax'], 'spectator_names' => []];
    $start = P2PNotifications::payload('started', $named);
    $fields = array_column($start['embeds'][0]['fields'], 'value', 'name');
    check($fields['Started by'] === 'Codex Web 733', 'Name the player who starts the match');
    check($fields['Players at start'] === "Codex Web 733\nggtothemax", 'List every starting human player');
    check($start['embeds'][0]['description'] === 'Codex Web 733 started the match.', 'Named start description');

    $watching = $named + ['participant_id' => 2, 'joined_name' => 'ggtothemax', 'joined_role' => 'spectator'];
    $watching['players'] = 1; $watching['player_names'] = ['Codex Web 733']; $watching['spectator_names'] = ['ggtothemax'];
    $playing = $named + ['participant_id' => 2, 'joined_name' => 'ggtothemax', 'joined_role' => 'player'];
    $count = count($sent);
    foreach ([$watching, $watching, $playing, $playing] as $join) {
        $notifier->enqueue('hot_joined', $join); $now += 5; $notifier->drain();
    }
    check(count($sent) === $count + 2, 'Spectating and promotion each announce once per participant');
    check($sent[$count]['embeds'][0]['description'] === 'ggtothemax joined the running match as a spectator.', 'Named spectator description');
    check($sent[$count+1]['embeds'][0]['description'] === 'ggtothemax joined to play in the running match.', 'Named player description');
    $another = $playing; $another['participant_id'] = 3; $another['joined_name'] = 'Chani';
    $another['players'] = 3; $another['player_names'][] = 'Chani';
    $notifier->enqueue('hot_joined', $another); $now += 5; $notifier->drain();
    check(count($sent) === $count + 3, 'Another hot join in the same room is not suppressed');

    $hostile = $playing; $hostile['host'] = '@everyone';
    $hostile['joined_name'] = '[link](https://example.com) *name*';
    $hostile['player_names'] = ['@everyone', $hostile['joined_name']];
    $safe = P2PNotifications::payload('hot_joined', $hostile);
    check($safe['allowed_mentions']['parse'] === [], 'Named hot joins cannot ping Discord');
    check(str_contains($safe['embeds'][0]['description'], '\\[link\\]\\('), 'Display names cannot create Markdown links');
    $long = $named; $long['players'] = 8; $long['player_names'] = array_fill(0,8,str_repeat('*',64));
    foreach (P2PNotifications::payload('started', $long)['embeds'][0]['fields'] as $field)
        check(strlen($field['value']) <= 1024, 'Long escaped rosters fit Discord field limits');
    foreach (['participant_id' => 0, 'joined_role' => 'host', 'joined_name' => "bad\nname",
              'player_names' => ['too','many','names'], 'spectator_names' => str_repeat('x',100)] as $key => $bad) {
        $invalidEvent = $playing; $invalidEvent[$key] = $bad;
        check(P2PNotifications::payload('hot_joined', $invalidEvent) === null, 'Reject invalid named event: '.$key);
    }
    $event['players'] = 99;
    check(P2PNotifications::payload('hosted', $event) === null, 'Reject impossible player counts');
    echo "P2P Discord payload, deduplication, privacy, pacing and retry checks passed\n";
} finally {
    foreach (glob($root . '/*/*') as $file) unlink($file);
    foreach (glob($root . '/*') as $dir) rmdir($dir);
    rmdir($root);
}
