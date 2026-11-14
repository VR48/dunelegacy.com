<?php
// Supporter memberships and sponsored features, verified without manual approval:
// PayPal payments are confirmed by PayPal's IPN (re-validated with PayPal) and crypto
// transfers are read from the chain. Clients never choose amounts, recipients, tokens, RPC
// or upstream URLs. Payer emails, names and wallet addresses are never stored or published;
// only the chosen alias is public.
require_once __DIR__ . '/feedback_service.php';

const DONATION_ENS = 'ggtothemax.eth';
const DONATION_ADDRESS = '0x03b4cdb1b6904e9de8a2dec8d55214c49f063114';
// Earlier transfers to the wallet are not donations and can never be claimed.
const DONATION_OPENED = 1794528000; // 2026-11-13T00:00:00Z
const DONATION_MEMBER_SECONDS = 365 * 86400;
// Rounding and ETH price movement between payment and verification.
const DONATION_TOLERANCE = 0.97;
// PayPal account email that receives payments (a forwarded dunelegacy.com alias).
const DONATION_PAYPAL_EMAIL = 'donations@dunelegacy.com';
const DONATION_SITE = 'https://dunelegacy.com';
const DONATION_TIERS = [
    'member' => ['bronze' => 20, 'silver' => 50, 'gold' => 100],
    'feature' => ['small' => 20, 'medium' => 50, 'large' => 100],
];
const DONATION_NETWORKS = [
    'ethereum' => ['label' => 'Ethereum', 'rpc' => 'https://ethereum-rpc.publicnode.com', 'confirmations' => 6,
        'tokens' => ['0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48' => ['USDC', 6],
            '0xdac17f958d2ee523a2206206994597c13d831ec7' => ['USDT', 6]]],
    'base' => ['label' => 'Base', 'rpc' => 'https://mainnet.base.org', 'confirmations' => 10,
        'tokens' => ['0x833589fcd6edb6e08f4c7c32d4f71b54bda02913' => ['USDC', 6]]],
];
const DONATION_TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';

function donationDatabase(string $path, bool $forcePython = false) {
    umask(0007);
    $db = !$forcePython && class_exists('PDO') && in_array('sqlite', PDO::getAvailableDrivers(), true)
        ? new PDO('sqlite:' . $path, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION])
        : new FeedbackPythonDatabase($path);
    $db->exec('PRAGMA busy_timeout=5000');
    // state: created (sent to PayPal), pending (PayPal still clearing), paid, reversed (refunded).
    // hidden=1 removes an entry from the public page without losing the record.
    $db->exec('CREATE TABLE IF NOT EXISTS donations (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, tier TEXT NOT NULL, usd INTEGER NOT NULL,
        alias TEXT NOT NULL, github TEXT NOT NULL, title TEXT NOT NULL, details TEXT NOT NULL,
        digest TEXT NOT NULL, method TEXT NOT NULL, payment_ref TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL, created INTEGER NOT NULL, paid INTEGER NOT NULL DEFAULT 0,
        received TEXT NOT NULL DEFAULT "", checked INTEGER NOT NULL DEFAULT 0,
        issue_url TEXT NOT NULL DEFAULT "", issue_attempted INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0)');
    $db->exec('CREATE TABLE IF NOT EXISTS donation_attempts (ip TEXT NOT NULL, created INTEGER NOT NULL)');
    return $db;
}

function donationHttp(string $method, string $url, string $headers, string $body): array {
    $context = stream_context_create(['http' => ['method' => $method, 'header' => $headers . "User-Agent: DuneCity-Donations\r\n",
        'content' => $body, 'timeout' => 12, 'ignore_errors' => true, 'follow_location' => 0],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true]]);
    $stream = @fopen($url, 'rb', false, $context);
    if(!$stream) return [0, null];
    $meta = stream_get_meta_data($stream);
    $response = stream_get_contents($stream, 2097153);
    fclose($stream);
    $status = 0;
    foreach($meta['wrapper_data'] ?? [] as $header) {
        if(preg_match('/^HTTP\/\S+ (\d{3})/', $header, $matches)) $status = (int)$matches[1];
    }
    if(strlen($response) > 2097152) return [0, null];
    return [$status, json_decode($response, true)];
}

function donationRpc(string $network, string $method, array $params) {
    [$status, $json] = donationHttp('POST', DONATION_NETWORKS[$network]['rpc'], "Content-Type: application/json\r\n",
        json_encode(['jsonrpc' => '2.0', 'id' => 1, 'method' => $method, 'params' => $params], JSON_THROW_ON_ERROR));
    if($status !== 200 || !is_array($json) || isset($json['error']) || !array_key_exists('result', $json))
        throw new RuntimeException('RPC unavailable');
    return $json['result'];
}

function donationEthPrice(int $time): float {
    [$status, $json] = donationHttp('GET', 'https://api.coinbase.com/v2/prices/ETH-USD/spot?date=' . gmdate('Y-m-d', $time), '', '');
    $amount = is_array($json) ? ($json['data']['amount'] ?? null) : null;
    if($status !== 200 || !is_numeric($amount) || (float)$amount <= 0) throw new RuntimeException('Price unavailable');
    return (float)$amount;
}

// PayPal IPN postback: true when PayPal vouches for the exact message, false when it does
// not, null when PayPal could not be reached (PayPal then retries the notification).
function donationIpnVerify(string $raw): ?bool {
    $context = stream_context_create(['http' => ['method' => 'POST', 'timeout' => 20, 'ignore_errors' => true,
        'follow_location' => 0, 'content' => 'cmd=_notify-validate&' . $raw,
        'header' => "Content-Type: application/x-www-form-urlencoded\r\nUser-Agent: DuneCity-Donations\r\nConnection: close\r\n"],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true]]);
    $response = @file_get_contents('https://ipnpb.paypal.com/cgi-bin/webscr', false, $context, 0, 64);
    if($response === false) return null;
    $response = trim($response);
    return $response === 'VERIFIED' ? true : ($response === 'INVALID' ? false : null);
}

// Production collaborators. Tests substitute every upstream call.
function donationIo(string $dataDir): array {
    $read = fn(string $name) => is_readable("$dataDir/$name") ? trim((string)file_get_contents("$dataDir/$name")) : '';
    $githubToken = $read('feedback_github_token.txt');
    $githubReady = $githubToken !== '' && !preg_match('/\s/', $githubToken);
    return [
        'now' => time(),
        'key' => $githubReady ? $githubToken : 'dunecity-donations',
        'rpc' => 'donationRpc',
        'price' => 'donationEthPrice',
        'github' => $githubReady ? fn($method, $path, $payload) => feedbackGithub($method, $path, $payload, $githubToken) : null,
        'ipn' => 'donationIpnVerify',
    ];
}

function donationText($value, int $min, int $max, bool $multiline): ?string {
    if(!is_string($value) || strlen($value) > $max * 4 + 16 || !preg_match('//u', $value)) return null;
    $value = trim(str_replace("\r\n", "\n", $value));
    $forbidden = $multiline ? '/[\x00-\x08\x0b-\x1f\x7f]/' : '/[\x00-\x1f\x7f]/';
    $length = preg_match_all('/./us', $value);
    return preg_match($forbidden, $value) || $length < $min || $length > $max ? null : $value;
}

function donationRequest(array $input) {
    $kind = $input['kind'] ?? ''; $tier = $input['tier'] ?? '';
    if(!is_string($kind) || !is_string($tier) || !isset(DONATION_TIERS[$kind][$tier]))
        return 'Choose a membership or a feature size.';
    $alias = donationText($input['alias'] ?? '', 2, 32, false);
    if($alias === null || !preg_match("/^[\\p{L}\\p{N}][\\p{L}\\p{N} ._'-]*\$/uD", $alias))
        return "Use 2–32 letters, numbers, spaces or . _ ' - for your alias.";
    $github = $input['github'] ?? '';
    $github = is_string($github) ? ltrim(trim($github), '@') : '-';
    if($github !== '' && !preg_match('/^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/D', $github))
        return 'Enter a valid GitHub username, or leave it blank.';
    $title = $details = '';
    if($kind === 'feature') {
        $title = donationText($input['title'] ?? '', 4, 100, false);
        $details = donationText($input['details'] ?? '', 10, 2000, true);
        if($title === null || $details === null)
            return 'Describe the feature: a 4–100 character title and 10–2000 characters of detail.';
    }
    $request = ['kind' => $kind, 'tier' => $tier, 'usd' => DONATION_TIERS[$kind][$tier], 'alias' => $alias,
        'github' => $github, 'title' => $title, 'details' => $details];
    $request['digest'] = hash('sha256', json_encode($request, JSON_THROW_ON_ERROR));
    return $request;
}

// Hashed per-address and global hourly caps on every upstream-reaching request.
function donationAdmit($db, string $ip, string $key, int $now): bool {
    $hash = hash_hmac('sha256', $ip, $key);
    $db->exec('BEGIN IMMEDIATE');
    try {
        $db->prepare('DELETE FROM donation_attempts WHERE created < ?')->execute([$now - 3600]);
        $query = $db->prepare('SELECT COUNT(*) FROM donation_attempts WHERE ip=?'); $query->execute([$hash]);
        $admitted = (int)$query->fetchColumn() < 30
            && (int)$db->query('SELECT COUNT(*) FROM donation_attempts')->fetchColumn() < 600;
        if($admitted) $db->prepare('INSERT INTO donation_attempts VALUES (?,?)')->execute([$hash, $now]);
        $db->exec('COMMIT');
        return $admitted;
    } catch(Throwable $error) { $db->exec('ROLLBACK'); throw $error; }
}

function donationRow($db, string $column, string $value) {
    $query = $db->prepare("SELECT * FROM donations WHERE $column=?"); $query->execute([$value]);
    return $query->fetch(2) ?: null;
}

function donationInsert($db, string $id, array $request, string $method, string $ref, string $state,
    int $now, int $paid, string $received): bool {
    $db->exec('BEGIN IMMEDIATE');
    try {
        if(donationRow($db, 'payment_ref', $ref)) { $db->exec('COMMIT'); return false; }
        $db->prepare('INSERT INTO donations(id,kind,tier,usd,alias,github,title,details,digest,method,payment_ref,state,created,paid,received)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')->execute([$id, $request['kind'], $request['tier'], $request['usd'],
            $request['alias'], $request['github'], $request['title'], $request['details'], $request['digest'],
            $method, $ref, $state, $now, $paid, $received]);
        $db->exec('COMMIT');
        return true;
    } catch(Throwable $error) { $db->exec('ROLLBACK'); throw $error; }
}

function donationHexInt($hex): int {
    if(!is_string($hex) || !preg_match('/^0x[0-9a-f]{1,15}$/iD', $hex)) throw new RuntimeException('Malformed quantity');
    return hexdec(substr($hex, 2));
}

function donationHexAmount($hex): float {
    if(!is_string($hex) || !preg_match('/^0x[0-9a-f]{0,64}$/iD', $hex)) throw new RuntimeException('Malformed amount');
    $value = 0.0;
    foreach(str_split(substr($hex, 2)) as $digit) $value = $value * 16 + hexdec($digit);
    return $value;
}

// Sums ETH sent directly and USDC/USDT Transfer events to the donation address in one transaction.
function donationChainPayment(string $network, string $tx, array $io): array {
    $chain = DONATION_NETWORKS[$network];
    $rpc = fn(string $method, array $params) => $io['rpc']($network, $method, $params);
    $receipt = $rpc('eth_getTransactionReceipt', [$tx]);
    if(!is_array($receipt)) return ['retry' => 'Waiting for the transaction to be confirmed…'];
    if(($receipt['status'] ?? '') !== '0x1') return ['error' => 'This transaction failed on-chain, so no payment arrived.'];
    $block = $receipt['blockNumber'] ?? '';
    $confirmations = donationHexInt($rpc('eth_blockNumber', [])) - donationHexInt($block) + 1;
    if($confirmations < $chain['confirmations'])
        return ['retry' => sprintf('Waiting for confirmations (%d of %d)…', max(0, $confirmations), $chain['confirmations'])];
    $header = $rpc('eth_getBlockByNumber', [$block, false]);
    $time = donationHexInt(is_array($header) ? ($header['timestamp'] ?? '') : '');
    if($time < DONATION_OPENED) return ['error' => 'This transaction was made before donations opened.'];
    $usd = 0.0; $parts = [];
    $transaction = $rpc('eth_getTransactionByHash', [$tx]);
    if(is_array($transaction) && strtolower((string)($transaction['to'] ?? '')) === DONATION_ADDRESS) {
        $eth = donationHexAmount($transaction['value'] ?? '0x0') / 1e18;
        if($eth > 0) {
            $usd += $eth * $io['price']($time);
            $parts[] = rtrim(rtrim(sprintf('%.6f', $eth), '0'), '.') . ' ETH';
        }
    }
    foreach(is_array($receipt['logs'] ?? null) ? $receipt['logs'] : [] as $log) {
        $token = $chain['tokens'][strtolower((string)($log['address'] ?? ''))] ?? null;
        $topics = $log['topics'] ?? null;
        if(!$token || !is_array($topics) || count($topics) !== 3 || strtolower((string)$topics[0]) !== DONATION_TRANSFER_TOPIC
            || strtolower((string)$topics[2]) !== '0x000000000000000000000000' . substr(DONATION_ADDRESS, 2)) continue;
        $amount = donationHexAmount($log['data'] ?? '') / 10 ** $token[1];
        $usd += $amount;
        $parts[] = sprintf('%.2f %s', $amount, $token[0]);
    }
    if($usd <= 0)
        return ['error' => 'This transaction did not send ETH, USDC or USDT to ' . DONATION_ENS . ' on ' . $chain['label'] . '.'];
    return ['usd' => $usd, 'received' => implode(' + ', $parts) . ' on ' . $chain['label'], 'time' => $time];
}

function donationCrypto(array $input, $db, array $io): array {
    $request = donationRequest($input);
    if(is_string($request)) return ['error' => $request];
    $network = $input['network'] ?? '';
    if(!is_string($network) || !isset(DONATION_NETWORKS[$network])) return ['error' => 'Choose the network you paid on.'];
    $tx = is_string($input['tx'] ?? null) ? strtolower(trim($input['tx'])) : '';
    if(!preg_match('/^0x[0-9a-f]{64}$/D', $tx)) return ['error' => 'Paste the transaction hash: 0x followed by 64 characters.'];
    $ref = "$network:$tx";
    $existing = donationRow($db, 'payment_ref', $ref);
    if($existing) return hash_equals($existing['digest'], $request['digest'])
        ? donationFinish($db, $existing, $io) : ['error' => 'This transaction has already been used for a donation.'];
    try { $payment = donationChainPayment($network, $tx, $io); }
    catch(Throwable $error) { return ['retry' => 'Could not reach the ' . DONATION_NETWORKS[$network]['label'] . ' network…']; }
    if(!isset($payment['usd'])) return $payment;
    if($payment['usd'] < $request['usd'] * DONATION_TOLERANCE)
        return ['error' => sprintf('This transaction sent about $%.2f (%s), below the $%d you selected. Choose an option at or below the amount sent.',
            $payment['usd'], $payment['received'], $request['usd'])];
    $id = bin2hex(random_bytes(16));
    if(!donationInsert($db, $id, $request, $network, $ref, 'paid', $io['now'], $payment['time'], $payment['received']))
        return ['error' => 'This transaction has already been used for a donation.'];
    return donationFinish($db, donationRow($db, 'id', $id), $io);
}

// Records the donor's choice and sends them to PayPal for that exact amount. The row stays
// 'created' until PayPal's IPN confirms a payment for it.
function donationPaypalStart(array $input, $db, array $io): array {
    $request = donationRequest($input);
    if(is_string($request)) return ['error' => $request];
    $id = bin2hex(random_bytes(16));
    donationInsert($db, $id, $request, 'paypal', 'paypal-order:' . $id, 'created', $io['now'], 0, '');
    $label = $request['kind'] === 'member'
        ? 'DuneCity ' . ucfirst($request['tier']) . ' supporter donation, 12 months'
        : 'DuneCity sponsored feature, ' . $request['tier'];
    return ['redirect' => 'https://www.paypal.com/cgi-bin/webscr?' . http_build_query([
        'cmd' => '_xclick', 'business' => DONATION_PAYPAL_EMAIL, 'item_name' => $label, 'item_number' => 'dunecity-' . $id,
        'amount' => $request['usd'] . '.00', 'currency_code' => 'USD', 'quantity' => 1, 'no_shipping' => 1, 'no_note' => 1,
        'custom' => $id, 'charset' => 'utf-8', 'rm' => 1,
        'notify_url' => DONATION_SITE . '/metaserver/donations.php?action=paypal-ipn',
        'return' => DONATION_SITE . '/support.html?paypal=' . $id,
        'cancel_return' => DONATION_SITE . '/support.html#donate',
    ], '', '&', PHP_QUERY_RFC3986)];
}

// Handles one PayPal IPN. Only messages PayPal itself vouches for, paid to our address in USD
// for at least the recorded amount, can change a donation. Returns false only when PayPal
// could not be reached, so the caller asks PayPal to resend.
function donationPaypalIpn(string $raw, array $fields, $db, array $io): bool {
    $verified = $io['ipn']($raw);
    if($verified === null) return false;
    if(!$verified) return true;
    $receivers = [strtolower(trim((string)($fields['business'] ?? ''))), strtolower(trim((string)($fields['receiver_email'] ?? '')))];
    $id = (string)($fields['custom'] ?? '');
    $row = preg_match('/^[0-9a-f]{32}$/D', $id) ? donationRow($db, 'id', $id) : null;
    $txn = (string)($fields['txn_id'] ?? '');
    if(!in_array(DONATION_PAYPAL_EMAIL, $receivers, true) || !$row || $row['method'] !== 'paypal'
        || !preg_match('/^[A-Z0-9]{8,32}$/D', $txn)) return true;
    $status = (string)($fields['payment_status'] ?? '');
    $parent = 'paypal:' . ($fields['parent_txn_id'] ?? '');
    if(in_array($status, ['Refunded', 'Reversed', 'Canceled_Reversal'], true)) {
        if($parent === $row['payment_ref']) $db->prepare('UPDATE donations SET state=? WHERE id=?')
            ->execute([$status === 'Canceled_Reversal' ? 'paid' : 'reversed', $id]);
        return true;
    }
    $state = ['Completed' => 'paid', 'Pending' => 'pending'][$status] ?? null;
    $gross = $fields['mc_gross'] ?? '';
    if($state === null || ($fields['mc_currency'] ?? '') !== 'USD' || !is_numeric($gross) || (float)$gross < $row['usd'])
        return true;
    $ref = 'paypal:' . $txn;
    $db->exec('BEGIN IMMEDIATE');
    try {
        $current = donationRow($db, 'id', $id);
        $owner = donationRow($db, 'payment_ref', $ref);
        $update = !in_array($current['state'], ['paid', 'reversed'], true) && (!$owner || $owner['id'] === $id)
            && in_array($current['payment_ref'], ['paypal-order:' . $id, $ref], true);
        if($update) $db->prepare('UPDATE donations SET state=?, payment_ref=?, paid=?, received=? WHERE id=?')
            ->execute([$state, $ref, $io['now'], sprintf('%.2f USD via PayPal', (float)$gross), $id]);
        $db->exec('COMMIT');
    } catch(Throwable $error) { $db->exec('ROLLBACK'); throw $error; }
    if($update && $state === 'paid') donationFinish($db, donationRow($db, 'id', $id), $io);
    return true;
}

// Polled by the donor's browser after returning from PayPal.
function donationPaypalStatus(array $input, $db, array $io): array {
    $id = $input['id'] ?? '';
    $row = is_string($id) && preg_match('/^[0-9a-f]{32}$/D', $id) ? donationRow($db, 'id', $id) : null;
    if(!$row || $row['method'] !== 'paypal') return ['error' => 'Unknown PayPal payment.'];
    if($row['state'] === 'created') return ['waiting' => 'Waiting for PayPal to confirm your payment…'];
    return donationFinish($db, $row, $io);
}

function donationFinish($db, array $row, array $io): array {
    if($row['state'] === 'pending')
        return ['pending' => 'PayPal is still clearing this payment. Your entry appears automatically once it does.'];
    if($row['state'] === 'reversed') return ['error' => 'This payment was refunded or reversed.'];
    if($row['state'] !== 'paid') return ['error' => 'This payment has not been confirmed.'];
    $result = ['ok' => true, 'kind' => $row['kind'], 'tier' => $row['tier'], 'alias' => $row['alias']];
    if($row['kind'] === 'member') return $result + ['until' => gmdate('Y-m-d', (int)$row['paid'] + DONATION_MEMBER_SECONDS)];
    $url = $row['issue_url'] !== '' ? $row['issue_url'] : donationCreateIssue($db, $row, $io);
    return $result + ['issue' => $url];
}

// One GitHub issue per paid feature. A previous attempt may have reached GitHub even if its
// response was lost, so it is looked up by marker before any repost.
function donationCreateIssue($db, array $row, array $io): string {
    if(!$io['github']) return '';
    $marker = '<!-- dunecity-donation:' . $row['id'] . ' -->';
    $url = '';
    if((int)$row['issue_attempted'] > 0) {
        [$status, $issues] = $io['github']('GET', 'issues?state=all&sort=created&direction=desc&per_page=100', null);
        if($status !== 200 || !is_array($issues)) return '';
        foreach($issues as $issue) {
            if(is_array($issue) && is_string($issue['body'] ?? null) && strpos($issue['body'], $marker) !== false) {
                $url = feedbackIssueUrl($issue); break;
            }
        }
        if($url === '' && $io['now'] - (int)$row['issue_attempted'] < 120) return '';
    }
    if($url === '') {
        $db->exec('BEGIN IMMEDIATE');
        try {
            $current = donationRow($db, 'id', $row['id']);
            $claimed = $current['issue_url'] === '' && (int)$current['issue_attempted'] === (int)$row['issue_attempted'];
            if($claimed) $db->prepare('UPDATE donations SET issue_attempted=? WHERE id=?')->execute([$io['now'], $row['id']]);
            $db->exec('COMMIT');
        } catch(Throwable $error) { $db->exec('ROLLBACK'); throw $error; }
        if(!$claimed) return $current['issue_url'];
        $size = ucfirst($row['tier']);
        $sponsor = $row['alias'] . ($row['github'] !== '' ? ' (@' . $row['github'] . ')' : '');
        [$status, $issue] = $io['github']('POST', 'issues', [
            'title' => "[Sponsored · $size] " . $row['title'],
            'body' => $row['details'] . "\n\n---\n**Sponsored feature:** $size (\$" . $row['usd'] . ")\n**Sponsor:** $sponsor\n"
                . '**Paid:** ' . $row['received'] . ' on ' . gmdate('Y-m-d', (int)$row['paid'])
                . "\n\nWe agree the scope and timing with the sponsor in this issue before work starts.\n\n" . $marker,
            'labels' => ['enhancement', 'sponsored-feature', 'size: ' . $row['tier']],
        ]);
        $url = $status === 201 ? feedbackIssueUrl($issue) : '';
    }
    if($url !== '') $db->prepare('UPDATE donations SET issue_url=? WHERE id=?')->execute([$url, $row['id']]);
    return $url;
}

// Bounded background completion: paid features whose issue could not be created yet. Choices
// that were never paid are deleted after two days so their text is not kept.
function donationReconcile($db, array $io): void {
    $db->prepare("DELETE FROM donations WHERE state='created' AND created < ?")->execute([$io['now'] - 2 * 86400]);
    $query = $db->prepare("SELECT * FROM donations WHERE checked < ? AND state='paid' AND kind='feature' AND issue_url=''
        ORDER BY checked LIMIT 2");
    $query->execute([$io['now'] - 600]);
    $rows = [];
    while($row = $query->fetch(2)) $rows[] = $row;
    foreach($rows as $row) {
        $db->prepare('UPDATE donations SET checked=? WHERE id=?')->execute([$io['now'], $row['id']]);
        try {
            donationCreateIssue($db, $row, $io);
        } catch(Throwable $error) {
            // Retried on a later page view.
        }
    }
}

function donationSupporters($db, array $io): array {
    donationReconcile($db, $io);
    $members = ['gold' => [], 'silver' => [], 'bronze' => []];
    $seen = [];
    $query = $db->prepare("SELECT alias, tier, paid FROM donations WHERE kind='member' AND state='paid' AND hidden=0
        AND paid >= ? ORDER BY usd DESC, paid ASC LIMIT 500");
    $query->execute([$io['now'] - DONATION_MEMBER_SECONDS]);
    while($row = $query->fetch(2)) {
        $key = strtolower($row['alias']);
        if(isset($seen[$key])) continue;
        $seen[$key] = true;
        $members[$row['tier']][] = ['alias' => $row['alias'], 'since' => gmdate('Y-m-d', (int)$row['paid'])];
    }
    $features = [];
    $query = $db->query("SELECT alias, tier, title, issue_url, paid FROM donations WHERE kind='feature' AND state='paid'
        AND hidden=0 ORDER BY paid DESC LIMIT 100");
    while($row = $query->fetch(2)) $features[] = ['alias' => $row['alias'], 'size' => $row['tier'], 'title' => $row['title'],
        'issue' => $row['issue_url'], 'date' => gmdate('Y-m-d', (int)$row['paid'])];
    $networks = [];
    foreach(DONATION_NETWORKS as $name => $chain) $networks[$name] = ['label' => $chain['label'],
        'assets' => array_merge(['ETH'], array_column(array_values($chain['tokens']), 0))];
    return ['members' => $members, 'features' => $features, 'tiers' => DONATION_TIERS, 'ens' => DONATION_ENS,
        'address' => DONATION_ADDRESS, 'networks' => $networks];
}
