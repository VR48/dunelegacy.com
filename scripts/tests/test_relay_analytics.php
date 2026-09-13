<?php
// Relay lifecycle receiver: authentication, key resolution, schema 1 + schema 2 records, the
// schema-1 -> schema-2 migration, and both storage backends. Everything lives in a disposable
// directory; nothing here touches a real database or a real key.
$directory = sys_get_temp_dir() . '/dune-relay-test-' . bin2hex(random_bytes(8));
mkdir($directory, 0700);
define('DATA_DIR', $directory);
require_once __DIR__ . '/../../metaserver/relay_analytics.php';
$usePython = in_array('--python', $_SERVER['argv'], true);

function recordRelayTest($event) {
    if (in_array("--python", $_SERVER["argv"], true)) {
        $result=analyticsPythonRequest("relay_record", ["event"=>$event]);
        if ($result !== null && ($result['status'] ?? '') === 'conflict') throw new RelayAnalyticsConflict();
        return $result !== null;
    }
    return relayAnalyticsRecord($event);
}
function checkRelay($condition, $message) {
    if (!$condition) throw new RuntimeException($message);
}
/** Runs one python3 snippet against the fixture database and returns [status, stderr]. */
function relayPython($script, array $arguments = []) {
    $process = proc_open(array_merge(['/usr/bin/python3', '-c', $script, ANALYTICS_DB_FILE], $arguments),
        [0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']], $pipes);
    fclose($pipes[0]); stream_get_contents($pipes[1]); fclose($pipes[1]);
    $error = stream_get_contents($pipes[2]); fclose($pipes[2]);
    return [proc_close($process), $error];
}
/** True if the event id was refused with a conflict rather than stored or silently accepted. */
function relayConflicts($event) {
    try { recordRelayTest($event); } catch (RelayAnalyticsConflict $error) { return true; }
    return false;
}
try {
    // --- an existing production-shaped database, before anything schema 2 touches it ----------
    // The frozen schema-1 table (CHECK(transport = 'wss')), one lifecycle row inside it, and an
    // unrelated legacy match row that the migration must leave alone.
    $seed = <<<'PYTHON'
import importlib.util,pathlib,sys
root=pathlib.Path(sys.argv[2])
spec=importlib.util.spec_from_file_location('store', root/'metaserver/analytics_store.py')
store=importlib.util.module_from_spec(spec); spec.loader.exec_module(store)
connection=store.open_database(sys.argv[1])
connection.executescript((root/'scripts/tests/fixtures/relay_analytics_schema1.sql').read_text())
connection.execute("INSERT INTO analytics_relay_events (event_id,room_id,kind,occurred_at,"
                   "received_at,participant_id,client_runtime,game_version,reason)"
                   " VALUES (?,?,?,?,?,?,?,?,?)",
                   ('event-'+'0'*32,'room-'+'0'*32,'joined',1789180000,1789180001,1,'browser','1.0.655',''))
store.record(connection, dict(phase='start', match_id='legacy-match', payload={}))
connection.commit()
assert "CHECK(transport = 'wss')" in connection.execute(
    "select sql from sqlite_master where name='analytics_relay_events'").fetchone()[0]
PYTHON;
    [$status, $error] = relayPython($seed, [realpath(__DIR__ . '/../..')]);
    checkRelay($status===0, 'schema 1 fixture: '.$error);

    // --- key resolution -----------------------------------------------------------------------
    $keyFile = $directory . '/analytics.key';
    $fileKey = str_repeat('file-key-', 5);
    file_put_contents($keyFile, $fileKey . "\n");
    chmod($keyFile, 0640);
    $inline = str_repeat('inline-key-', 4);
    checkRelay(relayAnalyticsResolveKey($inline, $keyFile) === $inline, 'inline key wins');
    checkRelay(relayAnalyticsResolveKey('', $keyFile) === $fileKey, 'named key file, newline trimmed');
    checkRelay(relayAnalyticsResolveKey(false, null, $keyFile) === $fileKey, 'fixed default key path');
    checkRelay(relayAnalyticsResolveKey(false, null, $directory . '/absent.key') === null, 'missing key file');
    checkRelay(relayAnalyticsResolveKey('short', $directory . '/absent.key') === null, 'short inline key refused');
    file_put_contents($directory . '/short.key', 'too-short');
    chmod($directory . '/short.key', 0640);
    checkRelay(relayAnalyticsResolveKey('', $directory . '/short.key') === null, 'short key file refused');
    file_put_contents($directory . '/spaces.key', str_repeat('has spaces ', 5));
    chmod($directory . '/spaces.key', 0640);
    checkRelay(relayAnalyticsResolveKey('', $directory . '/spaces.key') === null, 'malformed key file refused');
    file_put_contents($directory . '/world.key', $fileKey);
    chmod($directory . '/world.key', 0644);
    checkRelay(relayAnalyticsResolveKey('', $directory . '/world.key') === null, 'world-readable key file refused');
    checkRelay(relayAnalyticsResolveKey(false, false, $directory . '/world.key') === null,
        'world-readable fixed key path refused');

    // --- authentication -----------------------------------------------------------------------
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

    // --- schema 1 and schema 2 records ----------------------------------------------------------
    // A schema-1 body has no transport field and keeps meaning the wss relay it always meant.
    checkRelay(relayAnalyticsEvent($raw) === array_merge($event, ['transport'=>'wss']),
        'schema 1 defaults to the legacy wss transport');
    $poll = ['schema_version'=>2, 'event_id'=>'event-' . str_repeat('e',32),
             'room_id'=>'room-' . str_repeat('f',32), 'kind'=>'joined', 'occurred_at'=>1789190100,
             'participant_id'=>1, 'client_runtime'=>'browser', 'game_version'=>'1.0.700',
             'reason'=>'', 'transport'=>'https-poll'];
    checkRelay(relayAnalyticsEvent(json_encode($poll)) === $poll, 'schema 2 keeps the observed transport');
    foreach ([[1, ['transport'=>'wss']], [1, ['transport'=>'https-poll']], [2, []],
              [2, ['transport'=>'ws']], [2, ['transport'=>'WSS']], [2, ['transport'=>'wss ']],
              [2, ['transport'=>'udp']], [2, ['transport'=>'']], [2, ['transport'=>true]],
              [2, ['transport'=>['wss']]], [2, ['transport'=>null]], [3, ['transport'=>'wss']]] as $case) {
        [$version, $changes] = $case;
        $body = array_merge($event, ['schema_version'=>$version], $changes);
        if ($version === 2 && !array_key_exists('transport', $changes)) unset($body['transport']);
        checkRelay(relayAnalyticsEvent(json_encode($body)) === null,
            "schema $version with " . json_encode($changes) . ' refused');
    }
    foreach ([['ticket'=>'secret'],['participant_id'=>true],['kind'=>'started'],
              ['client_runtime'=>['browser']],['reason'=>str_repeat('x',49)],['schema_version'=>true]] as $changes) {
        checkRelay(relayAnalyticsEvent(json_encode(array_merge($event,$changes))) === null, 'invalid event refused');
    }

    // --- storage, migration and immutability ----------------------------------------------------
    checkRelay(recordRelayTest($event), 'schema 1 record');
    checkRelay(recordRelayTest($event), 'idempotent retry');
    checkRelay(recordRelayTest(relayAnalyticsEvent($raw)), 'normalised record is valid input again');
    $conflict = $event; $conflict['client_runtime']='native';
    checkRelay(relayConflicts($conflict), 'conflicting event id refused');
    $invalid=$event; $invalid['participant_id']=0;
    checkRelay(!recordRelayTest($invalid), 'internal caller validation');
    $forged=$event; $forged['transport']='https-poll';
    checkRelay(!recordRelayTest($forged), 'schema 1 may not relabel itself as https-poll');
    checkRelay(!recordRelayTest(array_merge($poll, ['transport'=>'udp'])), 'unknown transport refused');
    $native = $event; $native['event_id'] = 'event-' . str_repeat('c',32);
    $native['participant_id'] = 2; $native['client_runtime'] = 'native';
    checkRelay(recordRelayTest($native), 'native participant');
    checkRelay(recordRelayTest($poll), 'https-poll record');
    checkRelay(recordRelayTest($poll), 'https-poll idempotent retry');
    // Downgrade avoidance: the same id may not come back as a schema-1 (wss) event.
    $downgrade = $poll; unset($downgrade['transport']); $downgrade['schema_version'] = 1;
    checkRelay(relayConflicts($downgrade), 'schema 1 retry cannot relabel an https-poll event');
    checkRelay(relayConflicts(array_merge($poll, ['transport'=>'wss'])), 'transport is immutable');
    $pollNative = $poll; $pollNative['event_id'] = 'event-' . str_repeat('g',32);
    $pollNative['participant_id'] = 2; $pollNative['client_runtime'] = 'native';
    checkRelay(recordRelayTest($pollNative), 'native participant over https-poll');

    // --- what actually landed in SQLite ----------------------------------------------------------
    $script = <<<'PYTHON'
import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
rows=c.execute("select event_id,client_runtime,transport,source from analytics_relay_events order by event_id").fetchall()
assert [r[1:] for r in rows]==[
    ('browser','wss'    ,'relay_service_v1'),   # seeded schema-1 row, migrated untouched
    ('browser','wss'    ,'relay_service_v1'),
    ('native' ,'wss'    ,'relay_service_v1'),
    ('browser','https-poll','relay_service_v1'),
    ('native' ,'https-poll','relay_service_v1'),
], rows
assert c.execute("select client_runtime,transport,runtime_source,transport_source from analytics_relay_participants order by transport,participant_id").fetchall()==[
    ('browser','https-poll','client_reported','server_observed'),
    ('native' ,'https-poll','client_reported','server_observed'),
    ('browser','wss','client_reported','server_observed'),
    ('browser','wss','client_reported','server_observed'),
    ('native' ,'wss','client_reported','server_observed'),
], 'mixed transports and runtimes stay separable'
assert c.execute("select count(*) from analytics_matches where match_id='legacy-match'").fetchone()[0]==1, 'legacy match row preserved'
assert c.execute("select count(*) from sqlite_master where name='analytics_relay_room_idx'").fetchone()[0]==1, 'index preserved'
assert c.execute("select count(*) from sqlite_master where name like '%schema1%'").fetchone()[0]==0, 'no migration leftovers'
assert "https-poll" in c.execute("select sql from sqlite_master where name='analytics_relay_events'").fetchone()[0]
PYTHON;
    [$status, $error] = relayPython($script);
    checkRelay($status===0, 'database assertions: '.$error);
    $direct = array_merge($poll, ['schema_version'=>3, 'event_id'=>'direct-'.str_repeat('d',32), 'transport'=>'direct-p2p']);
    checkRelay(relayAnalyticsEvent(json_encode($direct)) !== null, 'schema 3 direct record accepted');
    checkRelay(recordRelayTest($direct) && recordRelayTest($direct), 'direct record retry is idempotent');
    checkRelay(relayAnalyticsNormalize(array_merge($direct,['schema_version'=>2])) === null, 'schema 2 cannot pretend to be direct');
    [$status,$error] = relayPython("import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); assert c.execute(\"select count(*),source from analytics_relay_events where transport='direct-p2p'\").fetchone()==(1,'signaling_service_v1')");
    checkRelay($status===0,'direct database assertions: '.$error);
    echo 'Relay analytics key resolution, schema 1 + 2 and ' . ($usePython ? 'Python fallback' : 'PDO')
        . " storage passed (migrated from the frozen schema-1 fixture)\n";
} finally {
    foreach (glob($directory . '/*') as $path) unlink($path);
    rmdir($directory);
}
