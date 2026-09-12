<?php
// Run against an isolated database: php scripts/tests/test_analytics_runtime.php /tmp/test/games.sqlite
if (PHP_SAPI !== 'cli' || $argc !== 2) exit(2);
define('ANALYTICS_DB_FILE', $argv[1]);
require __DIR__ . '/../../metaserver/analytics.php';
foreach ([null, 'unknown', 'bad', 1, ['browser']] as $invalid) {
    if (analyticsClientRuntime($invalid) !== null) throw new RuntimeException('Invalid runtime accepted');
}
foreach (['browser', 'native'] as $runtime) {
    if (analyticsClientRuntime($runtime) !== $runtime) throw new RuntimeException('Valid runtime lost');
}
foreach ([['start', 'php-browser-test', 'browser'], ['end', 'php-browser-test', null],
          ['end', 'php-native-test', 'native'], ['start', 'php-legacy-test', null]] as [$phase, $match, $runtime]) {
    $payload = ['schema_version' => 3, 'game_type' => 'skirmish', 'players' => []];
    if ($runtime !== null) $payload['client_runtime'] = $runtime;
    if (!analyticsRecordMatch($phase, $match, $payload)) throw new RuntimeException('Match write failed');
}
$summary = analyticsSummary();
if (!$summary['available'] || $summary['started'] < 3) throw new RuntimeException('Summary failed');
echo "PHP runtime validation and database writes passed\n";
