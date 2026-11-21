<?php
/** Read-only public aggregates and multiplayer history; cache and lock live outside DocumentRoot. */
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: public, max-age=60');
if (!in_array($_SERVER['REQUEST_METHOD'] ?? 'GET', ['GET', 'HEAD'], true)) {
    http_response_code(405); header('Allow: GET, HEAD'); exit;
}
$dataDir = getenv('DATA_DIR') ?: '/var/www/data';
$database = $dataDir.'/games.sqlite';
$cache = $dataDir.'/usage-public-v1.json';
$lockPath = $dataDir.'/usage-public.lock';
$historyLockPath = $dataDir.'/usage-history.lock';
/**
 * Sends the cached report with an exact length. A gzip copy written by the same refresh goes to
 * clients that accept it; an older or missing copy falls back to the plain file.
 */
function serveUsage(string $cache, bool $closeAfter = false): void {
    clearstatcache();
    $compressed = $cache.'.gz';
    $gzip = preg_match('/\bgzip\b/i', $_SERVER['HTTP_ACCEPT_ENCODING'] ?? '') === 1
        && is_file($compressed) && filemtime($compressed) >= filemtime($cache);
    $path = $gzip ? $compressed : $cache;
    if (function_exists('apache_setenv')) @apache_setenv('no-gzip', '1');
    header('Vary: Accept-Encoding');
    if ($gzip) header('Content-Encoding: gzip');
    header('Content-Length: '.filesize($path));
    if ($closeAfter) header('Connection: close');
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'HEAD') readfile($path);
}
/** Rebuilds the cache unless another request already is. No request parameter reaches the helper. */
function refreshUsage(string $database, string $cache, string $lockPath): void {
    $lock = @fopen($lockPath, 'c');
    if ($lock === false) return;
    if (flock($lock, LOCK_EX | LOCK_NB)) {
        try {
            clearstatcache();
            // A request that waited behind the lock may find the work already done.
            if (!is_file($cache) || time() - filemtime($cache) >= 300) {
                $result = runHelper(__DIR__.'/usage_stats.py', [$database], 4000000);
                if ($result !== null && ($result['data']['schema'] ?? null) === 1) {
                    $tmp = $cache.'.tmp';
                    if (file_put_contents($tmp, $result['body']) !== false && rename($tmp, $cache)) {
                        $packed = gzencode($result['body'], 6);
                        if ($packed !== false && file_put_contents($cache.'.gz.tmp', $packed) !== false) {
                            rename($cache.'.gz.tmp', $cache.'.gz');
                        }
                    }
                }
            }
        } finally { flock($lock, LOCK_UN); }
    }
    fclose($lock);
}
function unavailable(string $message, int $retryAfter): void {
    http_response_code(503);
    header('Retry-After: '.$retryAfter);
    header('Cache-Control: no-store');
    echo json_encode(['error' => $message]);
}
/** Runs the fixed helper. Arguments are this file's own paths plus an already validated integer. */
function runHelper(string $script, array $arguments, int $limit): ?array {
    $process = @proc_open(array_merge(['/usr/bin/python3', $script], $arguments),
        [0=>['pipe','r'], 1=>['pipe','w'], 2=>['file','/dev/null','a']], $pipes);
    if (!is_resource($process)) return null;
    fclose($pipes[0]);
    stream_set_timeout($pipes[1], 20);
    $body = stream_get_contents($pipes[1], $limit);
    $timedOut = stream_get_meta_data($pipes[1])['timed_out'];
    fclose($pipes[1]);
    if ($timedOut) proc_terminate($process);
    $code = proc_close($process);
    if ($timedOut || $code !== 0 || !is_string($body)) return null;
    $data = json_decode($body, true);
    return is_array($data) ? ['body' => $body, 'data' => $data] : null;
}
umask(0007);
// One page of game history: the only accepted parameter is a bounded positive integer. It
// reaches the helper as a page number and never a filesystem path, a file name or SQL text.
if (isset($_GET['history_page'])) {
    $requested = $_GET['history_page'];
    if (!is_string($requested) || !preg_match('/^[1-9][0-9]{0,6}$/D', $requested)) {
        http_response_code(400);
        header('Cache-Control: no-store');
        echo '{"error":"history_page must be a page number from 1 upwards."}';
        exit;
    }
    $page = (int)$requested;
    // A single history query at a time; a burst of page requests cannot fan out processes.
    $lock = @fopen($historyLockPath, 'c');
    $held = $lock !== false && flock($lock, LOCK_EX | LOCK_NB);
    if ($lock !== false && !$held) { usleep(250000); $held = flock($lock, LOCK_EX | LOCK_NB); }
    $result = $held ? runHelper(__DIR__.'/usage_stats.py',
        [$database, '--history-page', (string)$page], 400000) : null;
    if ($lock !== false) { if ($held) flock($lock, LOCK_UN); fclose($lock); }
    $data = $result['data'] ?? null;
    if (is_array($data) && ($data['schema'] ?? null) === 1 && ($data['page'] ?? null) === $page
        && is_array($data['games'] ?? null)) {
        if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'HEAD') echo $result['body'];
        exit;
    }
    unavailable('Game history is temporarily unavailable.', 5);
    exit;
}
if (is_file($cache) && time() - filemtime($cache) < 300) { serveUsage($cache); exit; }
if (is_file($cache)) {
    // Stale: answer at once with the last good report and rebuild after the response is complete,
    // so no visitor waits the helper's several seconds. The page fetches again shortly after.
    ignore_user_abort(true);
    serveUsage($cache, true);
    while (ob_get_level() > 0) ob_end_flush();
    flush();
    if (function_exists('fastcgi_finish_request')) fastcgi_finish_request();
    refreshUsage($database, $cache, $lockPath);
    exit;
}
// No report yet (first deployment): this request has to wait for one.
refreshUsage($database, $cache, $lockPath);
if (is_file($cache)) { serveUsage($cache); exit; }
unavailable('Usage statistics are temporarily unavailable.', 30);
