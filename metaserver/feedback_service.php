<?php
// Fixed-repository feedback service. Never accept a token, repository or upstream URL from a client.
function feedbackDatabase(string $path): PDO {
    $db = new PDO('sqlite:' . $path, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $db->exec('PRAGMA busy_timeout=5000');
    $db->exec('CREATE TABLE IF NOT EXISTS feedback_requests (
        id TEXT PRIMARY KEY, digest TEXT NOT NULL, created INTEGER NOT NULL,
        state TEXT NOT NULL, url TEXT NOT NULL DEFAULT "")');
    $db->exec('CREATE TABLE IF NOT EXISTS feedback_attempts (ip TEXT NOT NULL, created INTEGER NOT NULL)');
    return $db;
}
function feedbackGithub(string $method, string $path, ?array $payload, string $token): array {
    $body = $payload === null ? '' : json_encode($payload, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE);
    $headers = "Authorization: Bearer $token\r\nUser-Agent: DuneCity-Feedback\r\n"
        . "Accept: application/vnd.github+json\r\nX-GitHub-Api-Version: 2026-03-10\r\nContent-Type: application/json\r\n";
    $context = stream_context_create(['http' => ['method' => $method, 'header' => $headers,
        'content' => $body, 'timeout' => 12, 'ignore_errors' => true, 'follow_location' => 0],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true]]);
    $stream = @fopen('https://api.github.com/repos/VR48/dunecity/' . $path, 'rb', false, $context);
    if(!$stream) return [0, []];
    $meta = stream_get_meta_data($stream);
    $response = stream_get_contents($stream, 1048577);
    fclose($stream);
    $status = 0;
    foreach($meta['wrapper_data'] ?? [] as $header) {
        if(preg_match('/^HTTP\/\S+ (\d{3})/', $header, $matches)) $status = (int)$matches[1];
    }
    if(strlen($response) > 1048576) return [0, []];
    return [$status, json_decode($response, true) ?: []];
}
function feedbackIssueUrl($issue): string {
    $url = is_array($issue) ? ($issue['html_url'] ?? '') : '';
    return is_string($url) && preg_match('~^https://github\.com/VR48/dunecity/issues/[1-9][0-9]*$~D', $url) ? $url : '';
}
function feedbackHandle(string $method, array $input, string $ip, string $token, PDO $db, callable $github): string {
    if($method !== 'POST') return 'ERROR Use Send feedback in the game.';
    foreach(['request_id' => 32, 'title' => 400, 'details' => 8000, 'context' => 8000] as $key => $max) {
        if(!isset($input[$key]) || !is_string($input[$key]) || strlen($input[$key]) > $max
            || !preg_match('//u', $input[$key]) || preg_match('/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/', $input[$key]))
            return 'ERROR Invalid feedback. Please check the text and try again.';
    }
    if(!preg_match('/^[a-f0-9]{32}$/D', $input['request_id']) || trim($input['title']) === '' || trim($input['details']) === '')
        return 'ERROR Enter a summary and some feedback first.';
    if(preg_match_all('/./us', $input['title']) > 100 || preg_match_all('/./us', $input['details']) > 2000)
        return 'ERROR Please shorten the feedback.';
    if($token === '' || preg_match('/\s/', $token)) return 'ERROR Feedback service is not configured yet. Please try again later.';
    $id = $input['request_id'];
    $digest = hash('sha256', json_encode([$input['title'], $input['details'], $input['context']], JSON_THROW_ON_ERROR));
    $now = time();
    $db->exec('BEGIN IMMEDIATE');
    try {
        $query = $db->prepare('SELECT * FROM feedback_requests WHERE id=?'); $query->execute([$id]);
        $existing = $query->fetch(PDO::FETCH_ASSOC);
        if($existing && !hash_equals($existing['digest'], $digest)) {
            $db->exec('COMMIT'); return 'ERROR Request text changed. Please reopen the form and try again.';
        }
        if($existing && $existing['state'] === 'done') {
            $db->exec('COMMIT'); return 'OK ' . $existing['url'];
        }
        // Cap upstream calls, including retries. Hash the address; never attach it to issues.
        $ipHash = hash_hmac('sha256', $ip, $token);
        $db->prepare('DELETE FROM feedback_attempts WHERE created < ?')->execute([$now - 3600]);
        $query = $db->prepare('SELECT COUNT(*) FROM feedback_attempts WHERE ip=?'); $query->execute([$ipHash]);
        if((int)$query->fetchColumn() >= 6 || (int)$db->query('SELECT COUNT(*) FROM feedback_attempts')->fetchColumn() >= 60) {
            $db->exec('COMMIT'); return 'ERROR Feedback limit reached. Please try again in an hour.';
        }
        $db->prepare('INSERT INTO feedback_attempts VALUES (?,?)')->execute([$ipHash, $now]);
        if(!$existing) $db->prepare('INSERT INTO feedback_requests(id,digest,created,state) VALUES (?,?,?,?)')
            ->execute([$id, $digest, $now, 'pending']);
        if($existing && $existing['state'] === 'rejected')
            $db->prepare('UPDATE feedback_requests SET state=?,created=? WHERE id=?')->execute(['pending', $now, $id]);
        $db->exec('COMMIT');
    } catch(Throwable $error) { $db->exec('ROLLBACK'); throw $error; }
    $marker = '<!-- dunecity-feedback:' . $id . ' -->';
    if($existing && $existing['state'] === 'pending') {
        // A previous POST may have reached GitHub even if its response was lost. Never repost it.
        if($now - (int)$existing['created'] < 20) return 'ERROR Request is still being processed. Please try again shortly.';
        [$status, $issues] = $github('GET', 'issues?state=all&sort=created&direction=desc&per_page=100', null, $token);
        $url = '';
        if($status === 200 && is_array($issues)) foreach($issues as $issue) {
            if(is_array($issue) && strpos($issue['body'] ?? '', $marker) !== false) {
                $url = feedbackIssueUrl($issue); break;
            }
        }
        if($url === '') return 'ERROR Delivery is unconfirmed. Try again later; we will not post a duplicate.';
    } else {
        [$status, $issue] = $github('POST', 'issues', ['title' => '[Game feedback] ' . trim($input['title']),
            'body' => $input['details'] . "\n\n---\nGame details:\n" . $input['context'] . "\n\n" . $marker], $token);
        $url = $status === 201 ? feedbackIssueUrl($issue) : '';
        if($url === '') {
            // Explicit rejection is safe to retry; transport errors and 5xx remain pending.
            if(in_array($status, [400, 401, 403, 404, 422, 429], true))
                $db->prepare('UPDATE feedback_requests SET state=? WHERE id=?')->execute(['rejected', $id]);
            return 'ERROR Could not confirm delivery. Your text is still here; try again later.';
        }
    }
    $db->prepare('UPDATE feedback_requests SET state=?,url=? WHERE id=?')->execute(['done', $url, $id]);
    return 'OK ' . $url;
}
