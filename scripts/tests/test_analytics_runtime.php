<?php
// Run against an isolated database: php scripts/tests/test_analytics_runtime.php /tmp/test/games.sqlite
if (PHP_SAPI !== 'cli' || $argc !== 2) exit(2);
define('ANALYTICS_DB_FILE', $argv[1]);
require __DIR__ . '/../../metaserver/analytics.php';
foreach ([null, 'unknown', 'bad', 1, ['browser']] as $invalid) {
    if (analyticsClientRuntime($invalid) !== null) throw new RuntimeException('Invalid runtime accepted');
}
foreach (['browser', 'native', 'android', 'ios'] as $runtime) {
    if (analyticsClientRuntime($runtime) !== $runtime) throw new RuntimeException('Valid runtime lost');
}
foreach ([['start', 'php-browser-test', 'browser'], ['end', 'php-browser-test', null],
          ['end', 'php-native-test', 'native'], ['start', 'php-ios-test', 'ios'],
          ['start', 'php-legacy-test', null]] as [$phase, $match, $runtime]) {
    $payload = ['schema_version' => 3, 'game_type' => 'skirmish', 'players' => []];
    if ($runtime !== null) $payload['client_runtime'] = $runtime;
    if (!analyticsRecordMatch($phase, $match, $payload)) throw new RuntimeException('Match write failed');
}
// Reads through PDO, or through Python's sqlite3 when PHP runs without it (php -n fallback).
function testRows($sql) {
    $database = analyticsDatabase();
    if ($database !== null) return $database->query($sql)->fetchAll(PDO::FETCH_NUM);
    $process = proc_open(['/usr/bin/python3', '-c',
        'import json, sqlite3, sys; print(json.dumps(sqlite3.connect(sys.argv[1]).execute(sys.argv[2]).fetchall()))',
        ANALYTICS_DB_FILE, $sql], [1 => ['pipe', 'w']], $pipes);
    $output = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    if (proc_close($process) !== 0) throw new RuntimeException('Test query failed');
    return json_decode($output, true);
}
// An install id is stored only as a keyed hash, on the human row that carried it.
$raw = '0123456789abcdef0123456789abcdef';
$payload = ['schema_version' => 3, 'game_type' => 'multiplayer', 'install_id' => $raw, 'players' => [
    ['slot' => 0, 'player_name' => 'Host', 'controller' => 'human', 'install_id' => $raw],
    ['slot' => 1, 'player_name' => 'Bot', 'controller' => 'qbot', 'install_id' => str_repeat('f', 32)],
    ['slot' => 2, 'player_name' => 'Guest', 'controller' => 'human', 'install_id' => 'NOT-HEX'],
]];
foreach (['start', 'end'] as $phase) {
    if (!analyticsRecordMatch($phase, 'php-install-test', $payload)) throw new RuntimeException('Install id write failed');
}
$secret = trim((string)file_get_contents(dirname(ANALYTICS_DB_FILE) . '/analytics-install-key'));
$expected = substr(hash_hmac('sha256', $raw, hex2bin($secret)), 0, 32);
$keys = testRows("SELECT slot, install_key FROM analytics_players WHERE match_id = 'php-install-test' ORDER BY slot");
if ($keys !== [[0, $expected], [1, null], [2, null]]) throw new RuntimeException('Install key not stored as a keyed hash');
$stored = json_encode(testRows("SELECT start_json, end_json FROM analytics_matches"));
foreach ([$raw, str_repeat('f', 32), 'NOT-HEX', 'install_id'] as $private) {
    if (strpos($stored, $private) !== false) throw new RuntimeException('Raw install id stored');
}
$summary = analyticsSummary();
if (!$summary['available'] || $summary['started'] < 3) throw new RuntimeException('Summary failed');
echo "PHP runtime validation and database writes passed\n";
