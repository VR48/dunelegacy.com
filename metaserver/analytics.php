<?php
/**
 * Compact, versioned match analytics for the metaserver.
 *
 * This deliberately stores match summaries, not client logs.  A request is
 * limited to 20 KiB and expanded into queryable SQLite rows.  The original
 * JSON is retained only as a small forward-compatible envelope so a newer
 * client can add fields without requiring an immediate server migration.
 */

if (!defined('ANALYTICS_DB_FILE')) {
    define('ANALYTICS_DB_FILE', DATA_DIR . '/games.sqlite');
}
if (!defined('ANALYTICS_MAX_PAYLOAD_BYTES')) {
    define('ANALYTICS_MAX_PAYLOAD_BYTES', 20 * 1024);
}
if (!defined('ANALYTICS_MAX_PLAYERS')) {
    define('ANALYTICS_MAX_PLAYERS', 12);
}
if (!defined('ANALYTICS_MAX_UNIT_ROWS_PER_PLAYER')) {
    define('ANALYTICS_MAX_UNIT_ROWS_PER_PLAYER', 48);
}

function analyticsAvailable() {
    return class_exists('PDO') && in_array('sqlite', PDO::getAvailableDrivers(), true);
}

function analyticsPythonHelper() {
    $helper = __DIR__ . '/analytics_store.py';
    return is_file($helper) && is_readable($helper) && function_exists('proc_open') ? $helper : null;
}

function analyticsPythonRequest($action, array $request = []) {
    $helper = analyticsPythonHelper();
    if ($helper === null) return null;
    $request['action'] = $action;
    $request['database'] = ANALYTICS_DB_FILE;
    $encoded = json_encode($request, JSON_UNESCAPED_SLASHES);
    if ($encoded === false || strlen($encoded) > ANALYTICS_MAX_PAYLOAD_BYTES + 4096) return null;
    $pipes = [];
    $process = @proc_open(['/usr/bin/python3', $helper], [
        0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w'],
    ], $pipes);
    if (!is_resource($process)) return null;
    fwrite($pipes[0], $encoded);
    fclose($pipes[0]);
    $output = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    $error = stream_get_contents($pipes[2]);
    fclose($pipes[2]);
    $status = proc_close($process);
    if ($status !== 0) {
        error_log('Metaserver analytics helper unavailable: ' . trim($error));
        return null;
    }
    $response = json_decode($output, true);
    return is_array($response) && ($response['ok'] ?? false) ? $response : null;
}

function analyticsBackendAvailable() {
    return analyticsDatabase() !== null || analyticsPythonRequest('health') !== null;
}

function analyticsDatabase() {
    static $database = null;
    if ($database !== null) {
        return $database;
    }
    if (!analyticsAvailable()) {
        return null;
    }

    try {
        $database = new PDO('sqlite:' . ANALYTICS_DB_FILE, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $database->exec('PRAGMA journal_mode=WAL');
        $database->exec('PRAGMA synchronous=NORMAL');
        $database->exec('PRAGMA foreign_keys=ON');
        $database->exec('PRAGMA busy_timeout=3000');
        analyticsMigrate($database);
        return $database;
    } catch (Throwable $error) {
        error_log('Metaserver analytics unavailable: ' . $error->getMessage());
        return null;
    }
}

function analyticsMigrate(PDO $database) {
    $database->exec('CREATE TABLE IF NOT EXISTS analytics_matches (
        match_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL,
        started_at INTEGER,
        ended_at INTEGER,
        updated_at INTEGER NOT NULL,
        outcome TEXT NOT NULL DEFAULT "started",
        game_type TEXT,
        map_name TEXT,
        map_width INTEGER,
        map_height INTEGER,
        map_seed INTEGER,
        mod_name TEXT,
        mod_version TEXT,
        game_version TEXT,
        player_count INTEGER,
        human_count INTEGER,
        qbot_count INTEGER,
        duration_cycles INTEGER,
        duration_seconds INTEGER,
        winning_house INTEGER,
        total_spice_harvested INTEGER,
        total_units_destroyed INTEGER,
        total_structures_destroyed INTEGER,
        start_json TEXT,
        end_json TEXT
    )');
    $database->exec('CREATE TABLE IF NOT EXISTS analytics_players (
        match_id TEXT NOT NULL,
        slot INTEGER NOT NULL,
        house_id INTEGER,
        house_name TEXT,
        team INTEGER,
        controller TEXT,
        qbot_difficulty TEXT,
        result TEXT,
        final_credits INTEGER,
        spice_harvested INTEGER,
        units_built INTEGER,
        structures_built INTEGER,
        units_destroyed INTEGER,
        structures_destroyed INTEGER,
        units_lost INTEGER,
        structures_lost INTEGER,
        military_value INTEGER,
        city_population INTEGER,
        city_value INTEGER,
        PRIMARY KEY (match_id, slot),
        FOREIGN KEY (match_id) REFERENCES analytics_matches(match_id) ON DELETE CASCADE
    )');
    $database->exec('CREATE TABLE IF NOT EXISTS analytics_qbot_units (
        match_id TEXT NOT NULL,
        slot INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        item_name TEXT,
        target_weight_bps INTEGER,
        built INTEGER,
        lost INTEGER,
        destroyed INTEGER,
        reward_milli INTEGER,
        lost_value INTEGER,
        damage_value_milli INTEGER,
        kill_bonus_milli INTEGER,
        PRIMARY KEY (match_id, slot, item_id),
        FOREIGN KEY (match_id, slot) REFERENCES analytics_players(match_id, slot) ON DELETE CASCADE
    )');
    $database->exec('CREATE INDEX IF NOT EXISTS analytics_matches_started_idx ON analytics_matches(started_at)');
    $database->exec('CREATE INDEX IF NOT EXISTS analytics_matches_mode_idx ON analytics_matches(game_type, mod_name)');
    $database->exec('CREATE INDEX IF NOT EXISTS analytics_qbot_units_item_idx ON analytics_qbot_units(item_id)');
}

function analyticsString($value, $max = 128) {
    if (!is_string($value) && !is_numeric($value)) return null;
    $value = trim((string)$value);
    return substr(strip_tags($value), 0, $max);
}

function analyticsInt($value, $min = -2147483648, $max = 2147483647) {
    if (!is_int($value) && !is_float($value) && !is_numeric($value)) return null;
    $value = (int)$value;
    return max($min, min($max, $value));
}

function analyticsPayload($raw) {
    if (!is_string($raw) || strlen($raw) > ANALYTICS_MAX_PAYLOAD_BYTES) return null;
    $payload = json_decode($raw, true);
    return is_array($payload) ? $payload : null;
}

function analyticsGameType($value) {
    $value = analyticsString($value, 32);
    return in_array($value, ['campaign', 'skirmish', 'single_custom', 'multiplayer', 'load'], true) ? $value : 'unknown';
}

function analyticsOutcome($value) {
    $value = analyticsString($value, 16);
    return in_array($value, ['finished', 'abandoned'], true) ? $value : 'finished';
}

function analyticsMatchId($value) {
    $value = analyticsString($value, 96);
    return ($value !== null && preg_match('/^[A-Za-z0-9._-]{12,96}$/', $value)) ? $value : null;
}

function analyticsMatchFields(array $payload) {
    $summary = is_array($payload['summary'] ?? null) ? $payload['summary'] : [];
    return [
        'schema_version' => analyticsInt($payload['schema_version'] ?? 0, 0, 1000) ?? 0,
        'game_type' => analyticsGameType($payload['game_type'] ?? null),
        'map_name' => analyticsString($payload['map']['name'] ?? ($payload['map_name'] ?? null)),
        'map_width' => analyticsInt($payload['map']['width'] ?? null, 0, 2048),
        'map_height' => analyticsInt($payload['map']['height'] ?? null, 0, 2048),
        'map_seed' => analyticsInt($payload['map']['seed'] ?? null),
        'mod_name' => analyticsString($payload['mod']['name'] ?? ($payload['mod_name'] ?? 'vanilla')),
        'mod_version' => analyticsString($payload['mod']['version'] ?? null),
        'game_version' => analyticsString($payload['game_version'] ?? null),
        'player_count' => analyticsInt($summary['player_count'] ?? null, 0, ANALYTICS_MAX_PLAYERS),
        'human_count' => analyticsInt($summary['human_count'] ?? null, 0, ANALYTICS_MAX_PLAYERS),
        'qbot_count' => analyticsInt($summary['qbot_count'] ?? null, 0, ANALYTICS_MAX_PLAYERS),
        'outcome' => analyticsOutcome($summary['outcome'] ?? null),
        'duration_cycles' => analyticsInt($summary['duration_cycles'] ?? null, 0),
        'duration_seconds' => analyticsInt($summary['duration_seconds'] ?? null, 0),
        'winning_house' => analyticsInt($summary['winning_house'] ?? null, -1, 255),
        'total_spice_harvested' => analyticsInt($summary['total_spice_harvested'] ?? null, 0),
        'total_units_destroyed' => analyticsInt($summary['total_units_destroyed'] ?? null, 0),
        'total_structures_destroyed' => analyticsInt($summary['total_structures_destroyed'] ?? null, 0),
    ];
}

function analyticsStorePlayers(PDO $database, $matchId, array $players, $replace) {
    if ($replace) {
        $delete = $database->prepare('DELETE FROM analytics_players WHERE match_id = ?');
        $delete->execute([$matchId]);
    }
    $insert = $database->prepare('INSERT OR REPLACE INTO analytics_players
        (match_id, slot, house_id, house_name, team, controller, qbot_difficulty, result,
         final_credits, spice_harvested, units_built, structures_built, units_destroyed,
         structures_destroyed, units_lost, structures_lost, military_value, city_population, city_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');
    $unitInsert = $database->prepare('INSERT OR REPLACE INTO analytics_qbot_units
        (match_id, slot, item_id, item_name, target_weight_bps, built, lost, destroyed,
         reward_milli, lost_value, damage_value_milli, kill_bonus_milli)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');

    foreach (array_slice($players, 0, ANALYTICS_MAX_PLAYERS) as $slot => $player) {
        if (!is_array($player)) continue;
        $slot = analyticsInt($player['slot'] ?? $slot, 0, ANALYTICS_MAX_PLAYERS - 1);
        $controller = analyticsString($player['controller'] ?? 'unknown', 32);
        $insert->execute([
            $matchId, $slot, analyticsInt($player['house_id'] ?? null, 0, 255),
            analyticsString($player['house_name'] ?? null), analyticsInt($player['team'] ?? null, -1, 255),
            $controller, analyticsString($player['qbot_difficulty'] ?? null, 32),
            analyticsString($player['result'] ?? null, 16), analyticsInt($player['final_credits'] ?? null),
            analyticsInt($player['spice_harvested'] ?? null, 0), analyticsInt($player['units_built'] ?? null, 0),
            analyticsInt($player['structures_built'] ?? null, 0), analyticsInt($player['units_destroyed'] ?? null, 0),
            analyticsInt($player['structures_destroyed'] ?? null, 0), analyticsInt($player['units_lost'] ?? null, 0),
            analyticsInt($player['structures_lost'] ?? null, 0), analyticsInt($player['military_value'] ?? null, 0),
            analyticsInt($player['city_population'] ?? null, 0), analyticsInt($player['city_value'] ?? null, 0),
        ]);

        if ($controller !== 'qbot' || !is_array($player['qbot_units'] ?? null)) continue;
        foreach (array_slice($player['qbot_units'], 0, ANALYTICS_MAX_UNIT_ROWS_PER_PLAYER) as $unit) {
            if (!is_array($unit)) continue;
            $itemId = analyticsInt($unit['item_id'] ?? null, 0, 10000);
            if ($itemId === null) continue;
            $unitInsert->execute([
                $matchId, $slot, $itemId, analyticsString($unit['item_name'] ?? null),
                analyticsInt($unit['target_weight_bps'] ?? null, 0, 100000), analyticsInt($unit['built'] ?? null, 0),
                analyticsInt($unit['lost'] ?? null, 0), analyticsInt($unit['destroyed'] ?? null, 0),
                analyticsInt($unit['reward_milli'] ?? null, 0), analyticsInt($unit['lost_value'] ?? null, 0),
                analyticsInt($unit['damage_value_milli'] ?? null, 0), analyticsInt($unit['kill_bonus_milli'] ?? null, 0),
            ]);
        }
    }
}

function analyticsRecordMatch($phase, $matchId, array $payload, $source = 'v1') {
    $database = analyticsDatabase();
    if ($database === null) {
        return analyticsPythonRequest('record', [
            'phase' => $phase, 'match_id' => $matchId, 'payload' => $payload, 'source' => $source,
        ]) !== null;
    }
    $fields = analyticsMatchFields($payload);
    $now = time();
    $json = json_encode($payload, JSON_UNESCAPED_SLASHES);
    if ($json === false || strlen($json) > ANALYTICS_MAX_PAYLOAD_BYTES) return false;
    try {
        $database->beginTransaction();
        if ($phase === 'start') {
            $statement = $database->prepare('INSERT INTO analytics_matches
                (match_id, schema_version, source, started_at, updated_at, outcome, game_type, map_name,
                 map_width, map_height, map_seed, mod_name, mod_version, game_version, player_count,
                 human_count, qbot_count, start_json)
                VALUES (?, ?, ?, ?, ?, "started", ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET schema_version=excluded.schema_version,
                 source=excluded.source, updated_at=excluded.updated_at, game_type=excluded.game_type,
                 map_name=excluded.map_name, map_width=excluded.map_width, map_height=excluded.map_height,
                 map_seed=excluded.map_seed, mod_name=excluded.mod_name, mod_version=excluded.mod_version,
                 game_version=excluded.game_version, player_count=excluded.player_count,
                 human_count=excluded.human_count, qbot_count=excluded.qbot_count, start_json=excluded.start_json');
            $statement->execute([$matchId, $fields['schema_version'], $source, $now, $now, $fields['game_type'],
                $fields['map_name'], $fields['map_width'], $fields['map_height'], $fields['map_seed'],
                $fields['mod_name'], $fields['mod_version'], $fields['game_version'], $fields['player_count'],
                $fields['human_count'], $fields['qbot_count'], $json]);
            analyticsStorePlayers($database, $matchId, is_array($payload['players'] ?? null) ? $payload['players'] : [], true);
        } else {
            $statement = $database->prepare('INSERT INTO analytics_matches
                (match_id, schema_version, source, started_at, ended_at, updated_at, outcome, game_type, map_name,
                 map_width, map_height, map_seed, mod_name, mod_version, game_version, player_count, human_count,
                 qbot_count, duration_cycles, duration_seconds, winning_house, total_spice_harvested,
                 total_units_destroyed, total_structures_destroyed, end_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET ended_at=excluded.ended_at, updated_at=excluded.updated_at,
                 outcome=excluded.outcome, duration_cycles=excluded.duration_cycles,
                 duration_seconds=excluded.duration_seconds, winning_house=excluded.winning_house,
                 total_spice_harvested=excluded.total_spice_harvested,
                 total_units_destroyed=excluded.total_units_destroyed,
                 total_structures_destroyed=excluded.total_structures_destroyed, end_json=excluded.end_json');
            $statement->execute([$matchId, $fields['schema_version'], $source, $now, $now, $now, $fields['outcome'], $fields['game_type'],
                $fields['map_name'], $fields['map_width'], $fields['map_height'], $fields['map_seed'],
                $fields['mod_name'], $fields['mod_version'], $fields['game_version'], $fields['player_count'],
                $fields['human_count'], $fields['qbot_count'], $fields['duration_cycles'], $fields['duration_seconds'],
                $fields['winning_house'], $fields['total_spice_harvested'], $fields['total_units_destroyed'],
                $fields['total_structures_destroyed'], $json]);
            analyticsStorePlayers($database, $matchId, is_array($payload['players'] ?? null) ? $payload['players'] : [], true);
        }
        $database->commit();
        return true;
    } catch (Throwable $error) {
        if ($database->inTransaction()) $database->rollBack();
        error_log('Metaserver analytics write failed: ' . $error->getMessage());
        return false;
    }
}

function analyticsRecordLegacyStart($secret, $map, $modName, $version, $players) {
    $matchId = 'legacy-' . substr(hash('sha256', 'dunelegacy:' . $secret), 0, 48);
    $playerCount = $players === '' ? null : min(ANALYTICS_MAX_PLAYERS, count(explode(',', $players)));
    return analyticsRecordMatch('start', $matchId, [
        'schema_version' => 0, 'game_type' => 'multiplayer', 'map' => ['name' => $map],
        'mod' => ['name' => $modName], 'game_version' => $version,
        'summary' => ['player_count' => $playerCount],
    ], 'legacy_gamestart');
}

function analyticsSummary() {
    $database = analyticsDatabase();
    if ($database === null) {
        $summary = analyticsPythonRequest('summary');
        if ($summary === null) return ['available' => false];
        return ['available' => true, 'started' => (int)$summary['started'],
            'finished' => (int)$summary['finished'], 'last_30_days' => (int)$summary['last_30_days']];
    }
    try {
        $row = $database->query('SELECT COUNT(*) AS started, SUM(CASE WHEN ended_at IS NOT NULL THEN 1 ELSE 0 END) AS finished,
            SUM(CASE WHEN started_at >= strftime("%s", "now", "-30 days") THEN 1 ELSE 0 END) AS last_30_days
            FROM analytics_matches')->fetch();
        return ['available' => true, 'started' => (int)$row['started'], 'finished' => (int)$row['finished'],
            'last_30_days' => (int)$row['last_30_days']];
    } catch (Throwable $error) {
        error_log('Metaserver analytics summary failed: ' . $error->getMessage());
        return ['available' => false];
    }
}

function handleGameStats() {
    // v1 native clients use POST to keep the payload below Apache's request
    // line limit. Accept GET too for the browser fallback and manual probes.
    $request = $_SERVER['REQUEST_METHOD'] === 'POST' ? $_POST : $_GET;
    $phase = $request['phase'] ?? '';
    if ($phase === 'health') {
        if (!analyticsBackendAvailable()) {
            http_response_code(503);
            echo "ERROR: Analytics storage unavailable\n";
            return;
        }
        echo "OK\n";
        return;
    }
    $matchId = analyticsMatchId($request['match_id'] ?? null);
    $payload = analyticsPayload($request['stats'] ?? null);
    if (!in_array($phase, ['start', 'end'], true) || $matchId === null || $payload === null) {
        http_response_code(400);
        echo "ERROR: Invalid game stats payload\n";
        return;
    }
    if (!analyticsBackendAvailable()) {
        http_response_code(503);
        echo "ERROR: Analytics storage unavailable\n";
        return;
    }
    if (!analyticsRecordMatch($phase, $matchId, $payload)) {
        http_response_code(500);
        echo "ERROR: Unable to save game stats\n";
        return;
    }
    echo "OK\n";
}
