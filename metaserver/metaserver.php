<?php
/**
 * Dune Legacy Metaserver
 * 
 * Maintains a directory of active multiplayer game servers.
 * Game clients register their servers and query for available games.
 */

// Configuration
if (!defined('SERVER_TIMEOUT')) {
    define('SERVER_TIMEOUT', 60); // Servers expire after 60 seconds
}
if (!defined('DATA_DIR')) {
    define('DATA_DIR', getenv('DATA_DIR') ?: __DIR__);
}
if (!defined('DATA_FILE')) {
    define('DATA_FILE', DATA_DIR . '/servers.dat');
}
if (!defined('MAX_SERVERS')) {
    define('MAX_SERVERS', 100);
}
if (!defined('STATS_FILE')) {
    define('STATS_FILE', DATA_DIR . '/stats.json');
}
if (!defined('PUNCH_FILE')) {
    define('PUNCH_FILE', DATA_DIR . '/punch.dat');
}
// NAT traversal configuration
if (!defined('PUNCH_REQUEST_TTL')) {
    define('PUNCH_REQUEST_TTL', 60); // Punch requests expire after 60 seconds
}
if (!defined('PUNCH_READY_TTL')) {
    define('PUNCH_READY_TTL', 30); // Ready signals expire after 30 seconds
}
if (!defined('MAX_PUNCH_REQUESTS_PER_SESSION')) {
    define('MAX_PUNCH_REQUESTS_PER_SESSION', 5);
}
if (!defined('PUNCH_RATE_LIMIT_PER_IP')) {
    define('PUNCH_RATE_LIMIT_PER_IP', 10); // 10 requests per minute per IP
}

// Latest game version - update this when releasing new versions
if (!defined('LATEST_VERSION')) {
    define('LATEST_VERSION', '1.0.9');
}
if (!defined('DOWNLOAD_URL')) {
    define('DOWNLOAD_URL', 'https://dunelegacy.com/#download');
}

// Ensure data directory exists and is writable
if (!is_dir(DATA_DIR)) {
    @mkdir(DATA_DIR, 0755, true);
}
if (!is_writable(DATA_DIR)) {
    error_log("Warning: Data directory " . DATA_DIR . " is not writable");
}

/**
 * Get the real client IP address, accounting for load balancers and proxies
 */
function getRealClientIP() {
    // Check for common proxy headers (in order of preference)
    $headers = [
        'HTTP_X_FORWARDED_FOR',   // Most common (DigitalOcean App Platform uses this)
        'HTTP_X_REAL_IP',         // nginx
        'HTTP_CF_CONNECTING_IP',  // Cloudflare
        'HTTP_X_CLUSTER_CLIENT_IP', // Some load balancers
        'REMOTE_ADDR'             // Fallback to direct connection
    ];
    
    foreach ($headers as $header) {
        if (!empty($_SERVER[$header])) {
            // X-Forwarded-For can contain multiple IPs (client, proxy1, proxy2...)
            // We want the first one (the original client)
            if ($header === 'HTTP_X_FORWARDED_FOR') {
                $ips = explode(',', $_SERVER[$header]);
                $ip = trim($ips[0]);
            } else {
                $ip = $_SERVER[$header];
            }
            
            // Validate IP address
            if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)) {
                return $ip;
            }
            // If it's a private IP, keep checking other headers (might be behind multiple proxies)
            if (filter_var($ip, FILTER_VALIDATE_IP)) {
                // Save as fallback in case no public IP is found
                $fallbackIP = $ip;
            }
        }
    }
    
    // If no public IP found, use the fallback (might be for local development)
    return $fallbackIP ?? $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
}

// Only execute main routing if called directly (not included)
if (basename($_SERVER['PHP_SELF']) === 'metaserver.php') {
    header('Content-Type: text/plain');
    header('Cache-Control: no-cache, must-revalidate');
    
    // Get action from query string (support both 'action' and 'command' for backwards compatibility)
    $action = $_GET['action'] ?? $_GET['command'] ?? '';
    
    // Main routing
    switch($action) {
        case 'add':
            handleAdd();
            break;
        case 'update':
            handleUpdate();
            break;
        case 'remove':
            handleRemove();
            break;
        case 'list':
            handleList();
            break;
        case 'list2':
            handleList2();
            break;
        case 'version':
            handleVersion();
            break;
        case 'gamestart':
            handleGameStart();
            break;
        case 'punch_request':
            handlePunchRequest();
            break;
        case 'punch_poll':
            handlePunchPoll();
            break;
        case 'punch_ready':
            handlePunchReady();
            break;
        case 'punch_status':
            handlePunchStatus();
            break;
        default:
            echo "ERROR: Invalid action\n";
            echo "Valid actions: add, update, remove, list, list2, version, gamestart, punch_request, punch_poll, punch_ready, punch_status\n";
            echo "Use: ?action=list or ?command=list\n";
            http_response_code(400);
    }
}

/**
 * Add a new game server
 * Extended for NAT traversal: accepts stun_port, returns session_id
 */
function handleAdd() {
    // Get real client IP (handle load balancers/proxies)
    $ip = getRealClientIP();
    $port = intval($_GET['port'] ?? 0);
    $secret = sanitize($_GET['secret'] ?? '');
    $name = sanitize($_GET['gamename'] ?? $_GET['name'] ?? 'Unnamed Server');
    $map = sanitize($_GET['mapname'] ?? $_GET['map'] ?? 'Unknown');
    $numPlayers = intval($_GET['numplayers'] ?? 0);
    $maxPlayers = intval($_GET['maxplayers'] ?? 8);
    $version = sanitize($_GET['gameversion'] ?? $_GET['version'] ?? '0.0.0');
    $localIP = sanitize($_GET['localip'] ?? ''); // Optional local/LAN IP from client
    $modName = sanitize($_GET['modname'] ?? 'vanilla');
    $modVersion = sanitize($_GET['modversion'] ?? '');
    
    // NAT traversal: STUN-discovered external port (optional)
    $stunPort = isset($_GET['stun_port']) ? intval($_GET['stun_port']) : null;
    if ($stunPort !== null && ($stunPort < 1 || $stunPort > 65535)) {
        $stunPort = null; // Invalid, ignore
    }
    
    if ($port < 1 || $port > 65535) {
        echo "ERROR: Invalid port\n";
        return;
    }
    
    if (empty($secret)) {
        echo "ERROR: Secret required\n";
        return;
    }
    
    $servers = loadServers();
    
    // Use SECRET as the unique identifier, not IP+port
    // This correctly identifies the same game even if announced from different IPs
    $serverId = $secret;
    $isNewGame = !isset($servers[$serverId]);
    
    if (count($servers) >= MAX_SERVERS && $isNewGame) {
        echo "ERROR: Server list full\n";
        return;
    }
    
    // NAT traversal: Preserve session_id on re-add (same secret)
    // This is critical - punch flows depend on stable session_id
    if ($isNewGame) {
        $sessionId = bin2hex(random_bytes(6)); // 12 hex chars
    } else {
        // Preserve existing session_id
        $sessionId = $servers[$serverId]['sessionId'] ?? bin2hex(random_bytes(6));
        // Preserve existing stun_port unless new one provided
        if ($stunPort === null && isset($servers[$serverId]['stunPort'])) {
            $stunPort = $servers[$serverId]['stunPort'];
        }
    }
    
    $servers[$serverId] = [
        'ip' => $ip,
        'port' => $port,
        'secret' => $secret,
        'sessionId' => $sessionId,  // NAT traversal: public game identifier
        'stunPort' => $stunPort ?? $port,  // NAT traversal: STUN-discovered or fallback to port
        'name' => $name,
        'localIP' => $localIP, // Store local IP if provided
        'map' => $map,
        'numPlayers' => $numPlayers,
        'maxPlayers' => $maxPlayers,
        'version' => $version,
        'modName' => $modName,
        'modVersion' => $modVersion,
        'lastUpdate' => time()
    ];
    
    saveServers($servers);
    
    // Only record in statistics if it's a NEW game
    if ($isNewGame) {
        recordGameStart($name, $map, $maxPlayers, $version, $modName, $modVersion);
    }
    
    // Extended response for NAT traversal (old clients ignore extra lines)
    echo "OK\n";
    echo $secret . "\n";      // Line 2: secret (for backward compat)
    echo $sessionId . "\n";   // Line 3: session_id (new for NAT traversal)
}

/**
 * Update existing server status
 */
function handleUpdate() {
    $secret = sanitize($_GET['secret'] ?? '');
    
    if (empty($secret)) {
        echo "ERROR: Secret required\n";
        return;
    }
    
    $servers = loadServers();
    
    if (!isset($servers[$secret])) {
        echo "ERROR: Server not found\n";
        return;
    }
    
    // Update fields if provided
    if (isset($_GET['numplayers'])) {
        $servers[$secret]['numPlayers'] = intval($_GET['numplayers']);
    }
    if (isset($_GET['mapname']) || isset($_GET['map'])) {
        $servers[$secret]['map'] = sanitize($_GET['mapname'] ?? $_GET['map']);
    }
    
    $servers[$secret]['lastUpdate'] = time();
    
    saveServers($servers);
    echo "OK\n";
}

/**
 * Remove a server
 */
function handleRemove() {
    $secret = sanitize($_GET['secret'] ?? '');
    
    if (empty($secret)) {
        echo "ERROR: Secret required\n";
        return;
    }
    
    $servers = loadServers();
    
    if (isset($servers[$secret])) {
        // Clean up any punch data for this game
        $sessionId = $servers[$secret]['sessionId'] ?? '';
        if (!empty($sessionId)) {
            $punchData = loadPunchData();
            unset($punchData['requests'][$sessionId]);
            unset($punchData['ready'][$sessionId]);
            savePunchData($punchData);
        }
        
        unset($servers[$secret]);
        saveServers($servers);
    }
    
    echo "OK\n";
}

/**
 * List all active servers
 */
function handleList() {
    $servers = loadServers();
    $activeServers = [];
    $now = time();
    $changed = false;
    
    // Filter expired servers
    foreach ($servers as $id => $server) {
        if (($now - $server['lastUpdate']) <= SERVER_TIMEOUT) {
            $activeServers[$id] = $server;
        } else {
            $changed = true;
        }
    }
    
    // Save if we removed expired servers
    if ($changed) {
        saveServers($activeServers);
    }
    
    // Output server list in game-expected format:
    // OK\n
    // <ip>\t<port>\t<name>\t<version>\t<map>\t<numplayers>\t<maxplayers>\t<pwdprotected>\t<lastupdate>\t<localip>\t<modname>\t<modversion>\n
    echo "OK\n";
    foreach ($activeServers as $server) {
        echo sprintf(
            "%s\t%d\t%s\t%s\t%s\t%d\t%d\t%s\t%d\t%s\t%s\t%s\n",
            $server['ip'],
            $server['port'],
            $server['name'],
            $server['version'],
            $server['map'],
            $server['numPlayers'],
            $server['maxPlayers'],
            'false', // password protected (not implemented yet)
            $server['lastUpdate'],
            $server['localIP'] ?? '', // Local/LAN IP if provided
            $server['modName'] ?? 'vanilla',
            $server['modVersion'] ?? ''
        );
    }
}

/**
 * List all active servers (extended format with NAT traversal fields)
 * Returns 14 tab-separated fields including session_id and stun_port
 */
function handleList2() {
    $servers = loadServers();
    $activeServers = [];
    $now = time();
    $changed = false;
    
    // Filter expired servers
    foreach ($servers as $id => $server) {
        if (($now - $server['lastUpdate']) <= SERVER_TIMEOUT) {
            $activeServers[$id] = $server;
        } else {
            $changed = true;
        }
    }
    
    // Save if we removed expired servers
    if ($changed) {
        saveServers($activeServers);
    }
    
    // Output server list with NAT traversal fields:
    // OK\n
    // <ip>\t<port>\t<name>\t<version>\t<map>\t<numplayers>\t<maxplayers>\t<pwdprotected>\t<lastupdate>\t<localip>\t<modname>\t<modversion>\t<session_id>\t<stun_port>\n
    echo "OK\n";
    foreach ($activeServers as $server) {
        echo sprintf(
            "%s\t%d\t%s\t%s\t%s\t%d\t%d\t%s\t%d\t%s\t%s\t%s\t%s\t%d\n",
            $server['ip'],
            $server['port'],
            $server['name'],
            $server['version'],
            $server['map'],
            $server['numPlayers'],
            $server['maxPlayers'],
            'false', // password protected (not implemented yet)
            $server['lastUpdate'],
            $server['localIP'] ?? '', // Local/LAN IP if provided
            $server['modName'] ?? 'vanilla',
            $server['modVersion'] ?? '',
            $server['sessionId'] ?? '', // NAT traversal: public game identifier
            $server['stunPort'] ?? $server['port'] // NAT traversal: STUN-discovered port
        );
    }
}

// ============================================================================
// NAT TRAVERSAL: Hole Punch Coordination Endpoints
// ============================================================================

/**
 * Client requests hole punch coordination
 * GET: ?command=punch_request&session_id=<session_id>&stun_port=<stun_port>
 * Response: OK\n<client_id>\n or ERROR\n<message>\n
 */
function handlePunchRequest() {
    $sessionId = sanitize($_GET['session_id'] ?? '');
    $stunPort = intval($_GET['stun_port'] ?? 0);
    
    // Validate inputs
    if (empty($sessionId) || strlen($sessionId) !== 12 || !ctype_xdigit($sessionId)) {
        echo "ERROR\nInvalid session_id\n";
        return;
    }
    
    if ($stunPort < 1 || $stunPort > 65535) {
        echo "ERROR\nInvalid stun_port\n";
        return;
    }
    
    // Verify game exists
    $servers = loadServers();
    $gameFound = false;
    foreach ($servers as $server) {
        if (isset($server['sessionId']) && $server['sessionId'] === $sessionId) {
            $gameFound = true;
            break;
        }
    }
    
    if (!$gameFound) {
        echo "ERROR\nGame not found\n";
        return;
    }
    
    // Rate limit: 10 requests per minute per IP
    $clientIP = getRealClientIP();
    if (isPunchRateLimited($clientIP)) {
        echo "ERROR\nRate limited\n";
        return;
    }
    
    // Generate client_id and store punch request
    $clientId = bin2hex(random_bytes(8)); // 16 hex chars
    
    $punchData = loadPunchData();
    
    // Check max requests per session
    $requestCount = 0;
    if (isset($punchData['requests'][$sessionId])) {
        $requestCount = count($punchData['requests'][$sessionId]);
    }
    
    if ($requestCount >= MAX_PUNCH_REQUESTS_PER_SESSION) {
        echo "ERROR\nToo many requests for this game\n";
        return;
    }
    
    // Store the punch request
    if (!isset($punchData['requests'][$sessionId])) {
        $punchData['requests'][$sessionId] = [];
    }
    
    $punchData['requests'][$sessionId][$clientId] = [
        'client_ip' => $clientIP,  // Derived from connection, not client-provided
        'client_port' => $stunPort,
        'timestamp' => time(),
        'delivered' => false
    ];
    
    // Record rate limit
    recordPunchRequest($clientIP);
    
    savePunchData($punchData);
    
    echo "OK\n";
    echo $clientId . "\n";
}

/**
 * Host polls for pending punch requests
 * GET: ?command=punch_poll&secret=<host_secret>
 * Response: OK\n[<client_id>\t<client_ip>\t<client_port>\n...]
 */
function handlePunchPoll() {
    $secret = sanitize($_GET['secret'] ?? '');
    
    if (empty($secret)) {
        echo "ERROR\nInvalid secret\n";
        return;
    }
    
    // Find game by secret
    $servers = loadServers();
    if (!isset($servers[$secret])) {
        echo "ERROR\nGame not found\n";
        return;
    }
    
    $sessionId = $servers[$secret]['sessionId'] ?? '';
    if (empty($sessionId)) {
        echo "OK\n"; // No session_id means old game, no punch requests
        return;
    }
    
    $punchData = loadPunchData();
    cleanupExpiredPunchData($punchData);
    
    echo "OK\n";
    
    if (isset($punchData['requests'][$sessionId])) {
        foreach ($punchData['requests'][$sessionId] as $clientId => $request) {
            if (!$request['delivered']) {
                echo sprintf(
                    "%s\t%s\t%d\n",
                    $clientId,
                    $request['client_ip'],
                    $request['client_port']
                );
                // Mark as delivered
                $punchData['requests'][$sessionId][$clientId]['delivered'] = true;
            }
        }
        savePunchData($punchData);
    }
}

/**
 * Host signals ready to punch a specific client
 * GET: ?command=punch_ready&secret=<host_secret>&client_id=<client_id>
 * Response: OK\n or ERROR\n<message>\n
 */
function handlePunchReady() {
    $secret = sanitize($_GET['secret'] ?? '');
    $clientId = sanitize($_GET['client_id'] ?? '');
    
    if (empty($secret)) {
        echo "ERROR\nInvalid secret\n";
        return;
    }
    
    if (empty($clientId) || strlen($clientId) !== 16 || !ctype_xdigit($clientId)) {
        echo "ERROR\nInvalid client_id\n";
        return;
    }
    
    // Find game by secret
    $servers = loadServers();
    if (!isset($servers[$secret])) {
        echo "ERROR\nGame not found\n";
        return;
    }
    
    $server = $servers[$secret];
    $sessionId = $server['sessionId'] ?? '';
    
    if (empty($sessionId)) {
        echo "ERROR\nGame has no session_id\n";
        return;
    }
    
    $punchData = loadPunchData();
    
    // Store ready signal
    if (!isset($punchData['ready'][$sessionId])) {
        $punchData['ready'][$sessionId] = [];
    }
    
    $punchData['ready'][$sessionId][$clientId] = [
        'host_ip' => $server['ip'],
        'host_port' => $server['stunPort'] ?? $server['port'],
        'ready_at' => time()
    ];
    
    savePunchData($punchData);
    
    echo "OK\n";
}

/**
 * Client polls for punch readiness
 * GET: ?command=punch_status&session_id=<session_id>&client_id=<client_id>
 * Response: WAITING\n or READY\n<host_ip>\n<host_port>\n<punch_in_seconds>\n
 */
function handlePunchStatus() {
    $sessionId = sanitize($_GET['session_id'] ?? '');
    $clientId = sanitize($_GET['client_id'] ?? '');
    
    if (empty($sessionId) || strlen($sessionId) !== 12 || !ctype_xdigit($sessionId)) {
        echo "ERROR\nInvalid session_id\n";
        return;
    }
    
    if (empty($clientId) || strlen($clientId) !== 16 || !ctype_xdigit($clientId)) {
        echo "ERROR\nInvalid client_id\n";
        return;
    }
    
    $punchData = loadPunchData();
    cleanupExpiredPunchData($punchData);
    
    // Check if host has signaled ready
    if (!isset($punchData['ready'][$sessionId][$clientId])) {
        echo "WAITING\n";
        return;
    }
    
    $readyInfo = $punchData['ready'][$sessionId][$clientId];
    
    // Calculate punch delay (2 seconds from now)
    $punchInSeconds = 2;
    
    echo "READY\n";
    echo $readyInfo['host_ip'] . "\n";
    echo $readyInfo['host_port'] . "\n";
    echo $punchInSeconds . "\n";
    
    // Clean up - one-time read
    unset($punchData['ready'][$sessionId][$clientId]);
    if (empty($punchData['ready'][$sessionId])) {
        unset($punchData['ready'][$sessionId]);
    }
    savePunchData($punchData);
}

// ============================================================================
// NAT TRAVERSAL: Helper Functions
// ============================================================================

/**
 * Load punch data from file
 */
function loadPunchData() {
    if (!file_exists(PUNCH_FILE)) {
        return ['requests' => [], 'ready' => [], 'rate_limits' => []];
    }
    
    $data = @file_get_contents(PUNCH_FILE);
    if ($data === false) {
        return ['requests' => [], 'ready' => [], 'rate_limits' => []];
    }
    
    $punchData = @unserialize($data);
    if (!is_array($punchData)) {
        return ['requests' => [], 'ready' => [], 'rate_limits' => []];
    }
    
    return $punchData;
}

/**
 * Save punch data to file
 */
function savePunchData($punchData) {
    $data = serialize($punchData);
    @file_put_contents(PUNCH_FILE, $data, LOCK_EX);
}

/**
 * Cleanup expired punch data
 */
function cleanupExpiredPunchData(&$punchData) {
    $now = time();
    $changed = false;
    
    // Cleanup expired requests
    if (isset($punchData['requests'])) {
        foreach ($punchData['requests'] as $sessionId => &$requests) {
            foreach ($requests as $clientId => $request) {
                if (($now - $request['timestamp']) > PUNCH_REQUEST_TTL) {
                    unset($requests[$clientId]);
                    $changed = true;
                }
            }
            if (empty($requests)) {
                unset($punchData['requests'][$sessionId]);
            }
        }
    }
    
    // Cleanup expired ready signals
    if (isset($punchData['ready'])) {
        foreach ($punchData['ready'] as $sessionId => &$readySignals) {
            foreach ($readySignals as $clientId => $ready) {
                if (($now - $ready['ready_at']) > PUNCH_READY_TTL) {
                    unset($readySignals[$clientId]);
                    $changed = true;
                }
            }
            if (empty($readySignals)) {
                unset($punchData['ready'][$sessionId]);
            }
        }
    }
    
    // Cleanup old rate limit entries (older than 1 minute)
    if (isset($punchData['rate_limits'])) {
        foreach ($punchData['rate_limits'] as $ip => &$timestamps) {
            $timestamps = array_filter($timestamps, function($ts) use ($now) {
                return ($now - $ts) < 60;
            });
            if (empty($timestamps)) {
                unset($punchData['rate_limits'][$ip]);
            }
        }
    }
    
    if ($changed) {
        savePunchData($punchData);
    }
}

/**
 * Check if IP is rate limited for punch requests
 */
function isPunchRateLimited($ip) {
    $punchData = loadPunchData();
    $now = time();
    
    if (!isset($punchData['rate_limits'][$ip])) {
        return false;
    }
    
    // Count requests in the last minute
    $recentRequests = array_filter($punchData['rate_limits'][$ip], function($ts) use ($now) {
        return ($now - $ts) < 60;
    });
    
    return count($recentRequests) >= PUNCH_RATE_LIMIT_PER_IP;
}

/**
 * Record a punch request for rate limiting
 */
function recordPunchRequest($ip) {
    $punchData = loadPunchData();
    
    if (!isset($punchData['rate_limits'][$ip])) {
        $punchData['rate_limits'][$ip] = [];
    }
    
    $punchData['rate_limits'][$ip][] = time();
    
    savePunchData($punchData);
}

/**
 * Return latest version information for update checking
 * Response format:
 * OK\n
 * <latest_version>\t<download_url>\n
 */
function handleVersion() {
    $clientVersion = sanitize($_GET['gameversion'] ?? $_GET['version'] ?? '');
    
    echo "OK\n";
    echo LATEST_VERSION . "\t" . DOWNLOAD_URL . "\n";
    
    // Optionally log version checks for analytics
    if (!empty($clientVersion)) {
        $ip = getRealClientIP();
        error_log("Version check from $ip running $clientVersion (latest: " . LATEST_VERSION . ")");
    }
}

/**
 * Load servers from file
 */
function loadServers() {
    if (!file_exists(DATA_FILE)) {
        return [];
    }
    
    $data = @file_get_contents(DATA_FILE);
    if ($data === false) {
        return [];
    }
    
    $servers = @unserialize($data);
    return is_array($servers) ? $servers : [];
}

/**
 * Save servers to file
 */
function saveServers($servers) {
    $data = serialize($servers);
    @file_put_contents(DATA_FILE, $data, LOCK_EX);
}

/**
 * Sanitize user input
 */
function sanitize($str) {
    $str = strip_tags($str);
    $str = substr($str, 0, 255);
    return $str;
}

/**
 * Record a game start in statistics
 */
function recordGameStart($name, $map, $maxPlayers, $version, $modName = 'vanilla', $modVersion = '') {
    $stats = loadStats();
    
    // Initialize stats structure if needed
    if (!isset($stats['total_games'])) {
        $stats['total_games'] = 0;
        $stats['recent_games'] = [];
        $stats['popular_maps'] = [];
        $stats['popular_mods'] = [];
    }
    
    // Increment total games
    $stats['total_games']++;
    
    // Add to recent games (last 30 days)
    $stats['recent_games'][] = [
        'name' => $name,
        'map' => $map,
        'maxPlayers' => $maxPlayers,
        'version' => $version,
        'modName' => $modName,
        'modVersion' => $modVersion,
        'timestamp' => time()
    ];
    
    // Clean old games (older than 30 days)
    $monthAgo = time() - (30 * 24 * 60 * 60);
    $stats['recent_games'] = array_filter($stats['recent_games'], function($game) use ($monthAgo) {
        return $game['timestamp'] > $monthAgo;
    });
    
    // Update popular maps count
    if (!isset($stats['popular_maps'][$map])) {
        $stats['popular_maps'][$map] = 0;
    }
    $stats['popular_maps'][$map]++;
    
    // Update popular mods count
    if (!isset($stats['popular_mods'])) {
        $stats['popular_mods'] = [];
    }
    $modKey = $modName . ($modVersion ? ' v' . $modVersion : '');
    if (!isset($stats['popular_mods'][$modKey])) {
        $stats['popular_mods'][$modKey] = 0;
    }
    $stats['popular_mods'][$modKey]++;
    
    saveStats($stats);
    
    // Send Discord notification for new game
    sendDiscordNotification($name, $map, $maxPlayers, $version, $modName, $modVersion);
}

/**
 * Get statistics
 */
function getStats() {
    $stats = loadStats();
    
    // Clean old recent games
    if (isset($stats['recent_games'])) {
        $monthAgo = time() - (30 * 24 * 60 * 60);
        $stats['recent_games'] = array_filter($stats['recent_games'], function($game) use ($monthAgo) {
            return $game['timestamp'] > $monthAgo;
        });
    }
    
    // Sort popular maps by count
    if (isset($stats['popular_maps'])) {
        arsort($stats['popular_maps']);
    }
    
    // Sort popular mods by count
    if (isset($stats['popular_mods'])) {
        arsort($stats['popular_mods']);
    }
    
    return $stats;
}

/**
 * Load statistics from file
 */
function loadStats() {
    $defaultStats = [
        'total_games' => 0,
        'recent_games' => [],
        'popular_maps' => [],
        'popular_mods' => []
    ];
    
    if (!file_exists(STATS_FILE)) {
        return $defaultStats;
    }
    
    $data = @file_get_contents(STATS_FILE);
    if ($data === false) {
        return $defaultStats;
    }
    
    $stats = @json_decode($data, true);
    if (!is_array($stats)) {
        return $defaultStats;
    }
    
    // Ensure popular_mods exists for backward compatibility
    if (!isset($stats['popular_mods'])) {
        $stats['popular_mods'] = [];
    }
    
    return $stats;
}

/**
 * Save statistics to file
 */
function saveStats($stats) {
    $data = json_encode($stats, JSON_PRETTY_PRINT);
    @file_put_contents(STATS_FILE, $data, LOCK_EX);
}

/**
 * Handle game start notification (when countdown begins)
 */
function handleGameStart() {
    $secret = sanitize($_GET['secret'] ?? '');
    $map = sanitize($_GET['map'] ?? 'Unknown');
    $modName = sanitize($_GET['modname'] ?? 'vanilla');
    $players = sanitize($_GET['players'] ?? '');  // Format: "House1:Player1,House2:Player2,..."
    $version = sanitize($_GET['version'] ?? '');
    
    if (empty($secret)) {
        echo "ERROR: Secret required\n";
        return;
    }
    
    // Send Discord notification
    sendGameStartNotification($map, $modName, $players, $version);
    
    echo "OK\n";
}

/**
 * Send Discord webhook notification for game starting
 */
function sendGameStartNotification($map, $modName, $players, $version) {
    $logFile = DATA_DIR . '/discord.log';
    $log = function($msg) use ($logFile) {
        @file_put_contents($logFile, date('[Y-m-d H:i:s] ') . $msg . "\n", FILE_APPEND);
    };
    
    $log("Starting Game Start notification for map: $map");
    
    // Try config file first, then environment variable
    $configFile = DATA_DIR . '/discord_webhook.txt';
    if (file_exists($configFile)) {
        $webhookUrl = trim(file_get_contents($configFile));
    } else {
        $webhookUrl = getenv('DISCORD_WEBHOOK_URL');
    }
    
    if (empty($webhookUrl)) {
        $log("ERROR: No webhook URL configured");
        return;
    }
    
    // Parse players string into readable format
    // Input: "Atreides:Player1,Harkonnen:Player2,Ordos:QuantBot"
    // Output: formatted list
    $playerList = [];
    if (!empty($players)) {
        $pairs = explode(',', $players);
        foreach ($pairs as $pair) {
            $parts = explode(':', $pair, 2);
            if (count($parts) == 2) {
                $house = trim($parts[0]);
                $name = trim($parts[1]);
                $playerList[] = "**$house**: $name";
            }
        }
    }
    $playersDisplay = !empty($playerList) ? implode("\n", $playerList) : 'Unknown';
    
    $modDisplay = ($modName && $modName !== 'vanilla') ? $modName : 'vanilla';
    
    $payload = json_encode([
        'embeds' => [[
            'title' => '🚀 Game Starting!',
            'color' => 0x2ECC71, // Green for "go"
            'fields' => [
                ['name' => 'Map', 'value' => $map ?: 'Unknown', 'inline' => true],
                ['name' => 'Mod', 'value' => $modDisplay, 'inline' => true],
                ['name' => 'Version', 'value' => $version ?: '?', 'inline' => true],
                ['name' => 'Players', 'value' => $playersDisplay, 'inline' => false],
            ],
            'timestamp' => date('c')
        ]]
    ]);
    
    $log("Sending game start payload to Discord...");
    
    $ch = curl_init($webhookUrl);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 5
    ]);
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    
    if ($error) {
        $log("ERROR: curl failed - $error");
    } else {
        $log("Response: HTTP $httpCode - $response");
    }
}

/**
 * Send Discord webhook notification for new game
 */
function sendDiscordNotification($name, $map, $maxPlayers, $version, $modName, $modVersion) {
    $logFile = DATA_DIR . '/discord.log';
    $log = function($msg) use ($logFile) {
        @file_put_contents($logFile, date('[Y-m-d H:i:s] ') . $msg . "\n", FILE_APPEND);
    };
    
    $log("Starting Discord notification for: $name");
    
    // Try config file first, then environment variable
    $configFile = DATA_DIR . '/discord_webhook.txt';
    if (file_exists($configFile)) {
        $webhookUrl = trim(file_get_contents($configFile));
        $log("Webhook URL loaded from config file");
    } else {
        $webhookUrl = getenv('DISCORD_WEBHOOK_URL');
        $log("Config file not found, trying env var: " . ($webhookUrl ? "found" : "not found"));
    }
    
    if (empty($webhookUrl)) {
        $log("ERROR: No webhook URL configured");
        return;
    }
    
    $modDisplay = $modName;
    if ($modVersion) {
        $modDisplay .= ' v' . $modVersion;
    }
    
    $payload = json_encode([
        'embeds' => [[
            'title' => '🎮 New Game Hosted',
            'url' => 'https://dunelegacy.com/metaserver/',
            'color' => 0xE67E22, // Dune orange
            'fields' => [
                ['name' => 'Server', 'value' => $name ?: 'Unnamed', 'inline' => true],
                ['name' => 'Map', 'value' => $map ?: 'Unknown', 'inline' => true],
                ['name' => 'Players', 'value' => "0/$maxPlayers", 'inline' => true],
                ['name' => 'Version', 'value' => $version ?: '?', 'inline' => true],
                ['name' => 'Mod', 'value' => $modDisplay, 'inline' => true],
            ],
            'timestamp' => date('c')
        ]]
    ]);
    
    $log("Sending payload to Discord...");
    
    $ch = curl_init($webhookUrl);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 5
    ]);
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    
    if ($error) {
        $log("ERROR: curl failed - $error");
    } else {
        $log("Response: HTTP $httpCode - $response");
    }
}

