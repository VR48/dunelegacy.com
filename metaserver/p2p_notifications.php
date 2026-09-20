<?php
declare(strict_types=1);

/** Deployment-owned Discord delivery. Signaling only submits bounded, committed lobby facts. */
final class P2PNotifications
{
    public function __construct(private string $directory, private string $webhook,
                                private $transport = null, private $clock = null) {}

    private function now(): int { return $this->clock ? ($this->clock)() : time(); }

    public static function configured(): self
    {
        $file = DATA_DIR . '/discord_webhook.txt';
        $url = is_file($file) ? trim((string)file_get_contents($file)) : trim((string)getenv('DISCORD_WEBHOOK_URL'));
        return new self(DATA_DIR . '/discord-p2p', $url);
    }

    public static function payload(string $kind, array $event): ?array
    {
        if (!in_array($kind, ['hosted', 'started', 'hot_joined'], true)
            || !preg_match('/^[a-f0-9]{32}$/D', $event['room_log_id'] ?? '')
            || !in_array($event['mode'] ?? '', ['custom', 'coop'], true)
            || !in_array($event['visibility'] ?? '', ['public', 'private'], true)
            || !is_int($event['players'] ?? null) || $event['players'] < 1 || $event['players'] > 8
            || !is_int($event['max_players'] ?? null) || $event['max_players'] < $event['players'] || $event['max_players'] > 8
            || !preg_match('/^[A-Za-z0-9._-]{1,32}$/D', $event['version'] ?? '')
            || !is_string($event['host'] ?? null) || strlen($event['host']) > 64
            || preg_match('/[\x00-\x1f\x7f]/', $event['host'])) return null;
        foreach (['player_names', 'spectator_names'] as $key) {
            if (!array_key_exists($key, $event)) continue; // Older service snapshots remain deliverable.
            if (!is_array($event[$key]) || !array_is_list($event[$key]) || count($event[$key]) > 8) return null;
            foreach ($event[$key] as $name) if (!self::validName($name)) return null;
        }
        if (isset($event['player_names']) && count($event['player_names']) !== $event['players']) return null;
        if ($event['players'] + count($event['spectator_names'] ?? []) > $event['max_players']) return null;
        if ($kind === 'hot_joined' && (!self::validName($event['joined_name'] ?? null)
            || !in_array($event['joined_role'] ?? '', ['player', 'spectator'], true)
            || !is_int($event['participant_id'] ?? null) || $event['participant_id'] < 1 || $event['participant_id'] > 65535)) return null;
        $mode = $event['mode'] === 'coop' ? 'Campaign Co-op' : 'Custom Game';
        $private = $event['visibility'] === 'private';
        $fields = [
            ['name' => $kind === 'started' ? 'Started by' : 'Host', 'value' => self::displayName($event['host']), 'inline' => true],
            ['name' => 'Players', 'value' => $event['players'] . '/' . $event['max_players'], 'inline' => true],
            ['name' => 'Visibility', 'value' => $private ? 'Private' : 'Public', 'inline' => true],
            ['name' => 'Version', 'value' => $event['version'], 'inline' => true],
        ];
        if ($kind === 'hot_joined') $fields[] = [
            'name' => $event['joined_role'] === 'spectator' ? 'Joined as spectator' : 'Joined to play',
            'value' => self::displayName($event['joined_name']), 'inline' => false,
        ];
        foreach (['player_names' => $kind === 'started' ? 'Players at start' : 'Playing', 'spectator_names' => 'Spectators'] as $key => $label) {
            if (empty($event[$key])) continue;
            $names = array_map([self::class, 'displayName'], $event[$key]);
            $chunks = strlen(implode("\n", $names)) <= 1024 ? [$names] : array_chunk($names, 4);
            foreach ($chunks as $i => $chunk) $fields[] = ['name' => $label . ($i ? ' (continued)' : ''),
                'value' => implode("\n", $chunk), 'inline' => false];
        }
        // No invitation codes, addresses, session credentials or client-provided URLs ever enter a message.
        return ['allowed_mentions' => ['parse' => []], 'embeds' => [[
            'title' => $mode . match ($kind) { 'hosted' => ' Lobby Created', 'started' => ' Starting', default => ' Hot Join' },
            'url' => 'https://dunelegacy.com/play/',
            'description' => $kind === 'hot_joined' ? self::displayName($event['joined_name']) .
                ($event['joined_role'] === 'spectator' ? ' joined the running match as a spectator.' : ' joined to play in the running match.') :
                ($kind === 'started' ? self::displayName($event['host']) . ' started the match.' :
                ($private ? 'Private lobby — ask the host for an invitation.' : 'Open Join Online in the game to join this public lobby.')),
            'color' => match ($kind) { 'started' => 0x2ECC71, 'hot_joined' => 0x3498DB, default => 0xE67E22 },
            'fields' => $fields,
        ]]];
    }

    private static function validName(mixed $name): bool
    {
        return is_string($name) && $name !== '' && strlen($name) <= 64 && !preg_match('/[\x00-\x1f\x7f]/', $name);
    }

    private static function displayName(string $name): string
    {
        // Render player-chosen Markdown literally; allowed_mentions also prevents pings.
        $escaped = str_replace('\\', '\\\\', $name ?: 'Unnamed');
        foreach (['`', '*', '_', '~', '|', '>', '<', '[', ']', '(', ')'] as $char)
            $escaped = str_replace($char, '\\' . $char, $escaped);
        return $escaped;
    }

    private function enabled(): bool
    {
        return (bool)preg_match('~^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api(?:/v[0-9]+)?/webhooks/[0-9]+/[A-Za-z0-9_-]+(?:\?thread_id=[0-9]+)?$~D', $this->webhook);
    }

    /** Stable queue lock held only for local reads/writes, never during Discord delivery. */
    private function locked(callable $action, bool $nonblocking = false): void
    {
        if (!$this->enabled()) return;
        if (is_link($this->directory)) throw new RuntimeException('Unsafe notification directory');
        if (!is_dir($this->directory) && !mkdir($this->directory, 0700, true) && !is_dir($this->directory)) return;
        $lock = fopen($this->directory . '/queue.lock', 'c');
        if (!$lock) return;
        try {
            if (!flock($lock, LOCK_EX | ($nonblocking ? LOCK_NB : 0))) return;
            $file = $this->directory . '/queue.json';
            $raw = is_file($file) ? file_get_contents($file) : false;
            if ($raw !== false && strlen($raw) > 1048576) throw new RuntimeException('Notification queue oversized');
            $state = $raw === false ? ['jobs' => [], 'next' => 0] : json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
            $now = $this->now();
            foreach ($state['jobs'] as $id => $job) if ($job['expires'] < $now) unset($state['jobs'][$id]);
            $action($state, $now);
            $tmp = tempnam($this->directory, '.queue-');
            try {
                chmod($tmp, 0600);
                $json = json_encode($state, JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE);
                if (file_put_contents($tmp, $json) !== strlen($json) || !rename($tmp, $file)) throw new RuntimeException('Notification queue write failed');
            } finally { if (is_file($tmp)) unlink($tmp); }
        } finally { flock($lock, LOCK_UN); fclose($lock); }
    }

    public function enqueue(string $kind, array $event): void
    {
        $payload = self::payload($kind, $event);
        if ($payload === null) return;
        $identity = $kind === 'hot_joined' ? ':' . $event['participant_id'] . ':' . $event['joined_role'] : '';
        $id = hash('sha256', $event['room_log_id'] . ':' . $kind . $identity);
        $this->locked(static function (array &$state, int $now) use ($id, $payload): void {
            if (isset($state['jobs'][$id])) return;
            if (count($state['jobs']) >= 256) {
                foreach ($state['jobs'] as $old => $job) {
                    if ($job['done']) { unset($state['jobs'][$old]); break; }
                }
            }
            if (count($state['jobs']) >= 256) { error_log('P2P Discord queue full'); return; }
            $state['jobs'][$id] = ['payload' => $payload, 'attempts' => 0, 'due' => $now,
                'expires' => $now + 3600, 'done' => false];
        });
    }

    /** At most one delivery per request, globally paced; failed deliveries retry on later traffic. */
    public function drain(): void
    {
        $selected = null;
        $this->locked(static function (array &$state, int $now) use (&$selected): void {
            if ($state['next'] > $now) return;
            foreach ($state['jobs'] as $id => &$job) {
                if ($job['done'] || $job['due'] > $now) continue;
                $job['attempts']++;
                $job['due'] = $now + 30; // lease survives an interrupted sender
                $state['next'] = $now + 5;
                $selected = ['id' => $id, 'payload' => $job['payload']];
                break;
            }
        }, true);
        if ($selected === null) return;
        // No queue or room lock is held across the network request.
        try { $result = $this->transport ? ($this->transport)($selected['payload']) : $this->send($selected['payload']); }
        catch (Throwable) { $result = ['status' => 0]; }
        $status = (int)($result['status'] ?? 0);
        $message = (string)($result['message_id'] ?? '');
        $success = $status === 200 && preg_match('/^[0-9]+$/D', $message);
        $this->locked(static function (array &$state, int $now) use ($selected, $result, $status, $success): void {
            $id = $selected['id'];
            if (!isset($state['jobs'][$id])) return;
            $job = &$state['jobs'][$id];
            $job['done'] = (bool)$success || $job['attempts'] >= 5 || in_array($status, [400, 401, 403, 404], true);
            $delay = max(5, min(900, (int)ceil((float)($result['retry_after'] ?? 30))));
            $state['next'] = max($state['next'], $now + ($success ? 5 : $delay));
            $job['due'] = $now + $delay;
        });
        // Keep IDs/status only; never log webhook URL/token or player names.
        $diagnostic = 'P2P Discord ' . ($success ? 'delivered' : 'failed') . ' event=' . $selected['id'] . ' status=' . $status
            . ($success ? ' message=' . $message : '');
        error_log($diagnostic);
        if (defined('DATA_DIR')) {
            @file_put_contents(DATA_DIR . '/discord.log', date('[Y-m-d H:i:s] ') . $diagnostic . "\n", FILE_APPEND | LOCK_EX);
        }
    }

    private function send(array $payload): array
    {
        if (!function_exists('curl_init')) return ['status' => 0];
        // wait=true requires Discord to confirm a saved message, rather than accepting an empty 204.
        $url = $this->webhook . (str_contains($this->webhook, '?') ? '&' : '?') . 'wait=true';
        $curl = curl_init($url);
        curl_setopt_array($curl, [CURLOPT_POST => true, CURLOPT_POSTFIELDS => json_encode($payload, JSON_INVALID_UTF8_SUBSTITUTE),
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'], CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT_MS => 800, CURLOPT_TIMEOUT_MS => 2000, CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_PROTOCOLS => CURLPROTO_HTTPS]);
        $body = curl_exec($curl);
        $status = (int)curl_getinfo($curl, CURLINFO_HTTP_CODE);
        curl_close($curl);
        $answer = is_string($body) ? json_decode($body, true) : null;
        return ['status' => $status, 'message_id' => $answer['id'] ?? '', 'retry_after' => $answer['retry_after'] ?? 30];
    }
}
