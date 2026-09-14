<?php
/** Read-only public aggregates; cache and lock live outside DocumentRoot. */
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: public, max-age=60');
if (!in_array($_SERVER['REQUEST_METHOD'] ?? 'GET', ['GET', 'HEAD'], true)) {
    http_response_code(405); header('Allow: GET, HEAD'); exit;
}
$cache = '/var/www/data/usage-public-v1.json';
$lockPath = '/var/www/data/usage-public.lock';
function serveUsage(string $cache): void {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'HEAD') readfile($cache);
}
if (is_file($cache) && time() - filemtime($cache) < 300) { serveUsage($cache); exit; }
umask(0007);
$lock = @fopen($lockPath, 'c');
if ($lock !== false && flock($lock, LOCK_EX | LOCK_NB)) {
    try {
        // No request parameter reaches the helper, filesystem paths or SQL.
        $process = @proc_open(['/usr/bin/python3', __DIR__.'/usage_stats.py'],
            [0=>['pipe','r'], 1=>['pipe','w'], 2=>['file','/dev/null','a']], $pipes);
        if (is_resource($process)) {
            fclose($pipes[0]);
            stream_set_timeout($pipes[1], 20);
            $body = stream_get_contents($pipes[1], 4000000);
            $timedOut = stream_get_meta_data($pipes[1])['timed_out'];
            fclose($pipes[1]);
            if ($timedOut) proc_terminate($process);
            $code = proc_close($process);
            $data = is_string($body) ? json_decode($body, true) : null;
            if (!$timedOut && $code === 0 && is_array($data) && ($data['schema'] ?? null) === 1) {
                $tmp = $cache.'.tmp';
                if (file_put_contents($tmp, $body) !== false) rename($tmp, $cache);
            }
        }
    } finally { flock($lock, LOCK_UN); fclose($lock); }
} elseif (is_resource($lock)) fclose($lock);
if (is_file($cache)) { serveUsage($cache); exit; }
http_response_code(503);
header('Retry-After: 30');
header('Cache-Control: no-store');
echo '{"error":"Usage statistics are temporarily unavailable."}';
