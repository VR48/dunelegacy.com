<?php
$directory = sys_get_temp_dir() . '/dune-relay-test-' . bin2hex(random_bytes(8));
mkdir($directory, 0700);
define('DATA_DIR', $directory);
require_once __DIR__ . '/../../metaserver/relay_analytics.php';
function recordRelayTest($event) {
    if (in_array("--python", $_SERVER["argv"], true)) return analyticsPythonRequest("relay_record", ["event"=>$event]) !== null;
    return relayAnalyticsRecord($event);
}
function checkRelay($condition, $message) {
    if (!$condition) throw new RuntimeException($message);
}
try {
    $event = ['schema_version'=>1, 'event_id'=>'event-' . str_repeat('a',32),
              'room_id'=>'room-' . str_repeat('b',32), 'kind'=>'joined', 'occurred_at'=>1789190000,
              'participant_id'=>1, 'client_runtime'=>'browser', 'game_version'=>'1.0.655', 'reason'=>''];
    $raw = json_encode($event);
    $timestamp = '1789190000';
    $key = str_repeat('test-key-', 8);
    $signature = hash_hmac('sha256', $timestamp . "\n" . $raw, $key);
    checkRelay(relayAnalyticsAuthenticate($raw,$timestamp,$signature,$key,1789190000), 'valid authentication');
    foreach ([[$raw . ' ', $timestamp, $signature, $key, 1789190000],
              [$raw, $timestamp, $signature, $key, 1789190301],
              [$raw, $timestamp, $signature, 'short', 1789190000],
              [$raw, $timestamp, 'invalid', $key, 1789190000],
              [str_repeat('x',4097),$timestamp,$signature,$key,1789190000]] as $args) {
        checkRelay(!relayAnalyticsAuthenticate(...$args), 'invalid authentication refused');
    }
    checkRelay(relayAnalyticsEvent($raw) === $event, 'valid event');
    foreach ([['ticket'=>'secret'],['transport'=>'udp'],['participant_id'=>true],['kind'=>'started'],
              ['client_runtime'=>['browser']],['reason'=>str_repeat('x',49)],['schema_version'=>true]] as $changes) {
        checkRelay(relayAnalyticsEvent(json_encode(array_merge($event,$changes))) === null, 'invalid event refused');
    }
    checkRelay(recordRelayTest($event), 'record');
    checkRelay(recordRelayTest($event), 'idempotent retry');
    $native = $event; $native['event_id'] = 'event-' . str_repeat('c',32);
    $native['participant_id'] = 2; $native['client_runtime'] = 'native';
    checkRelay(recordRelayTest($native), 'native participant');
    $script = 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); '
        . 'assert c.execute("select count(*) from analytics_relay_events").fetchone()[0]==2; '
        . 'assert c.execute("select client_runtime from analytics_relay_participants order by participant_id").fetchall()==[("browser",),("native",)]';
    $process = proc_open(['/usr/bin/python3','-c',$script,ANALYTICS_DB_FILE], [0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']], $pipes);
    fclose($pipes[0]); $output=stream_get_contents($pipes[1]); fclose($pipes[1]);
    $error=stream_get_contents($pipes[2]); fclose($pipes[2]);
    checkRelay(proc_close($process)===0, 'database assertions: '.$error);
    echo 'Relay analytics authentication and ' . (in_array('--python', $_SERVER['argv'], true) ? 'Python fallback' : 'PDO') . " storage passed\n";
} finally {
    foreach (glob($directory . '/*') as $path) unlink($path);
    rmdir($directory);
}
