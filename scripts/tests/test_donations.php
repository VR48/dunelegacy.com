<?php
require_once __DIR__ . '/../../metaserver/donations_service.php';
function check($value, $message) { if(!$value) throw new RuntimeException($message); }

const NOW = DONATION_OPENED + 30 * 86400;
const OURS = '0x000000000000000000000000' . '03b4cdb1b6904e9de8a2dec8d55214c49f063114';
const BASE_USDC = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913';

function tx(string $digit): string { return '0x' . str_repeat($digit, 64); }
function usdc(int $dollars, string $to = OURS, string $token = BASE_USDC): array {
    return ['address' => $token, 'topics' => [DONATION_TRANSFER_TOPIC, '0x' . str_repeat('0', 24) . str_repeat('9', 40), $to],
        'data' => '0x' . str_pad(dechex($dollars * 1000000), 64, '0', STR_PAD_LEFT)];
}

// Fake chain: hash => [receipt logs, eth wei hex, block number, block time, status, to]
function chain(array &$chain): callable {
    return function($network, $method, $params) use (&$chain) {
        if($method === 'eth_blockNumber') return '0x' . dechex(1000);
        if($method === 'eth_getBlockByNumber') {
            foreach($chain as $tx) if('0x' . dechex($tx['block']) === $params[0]) return ['timestamp' => '0x' . dechex($tx['time'])];
            return null;
        }
        $tx = $chain[$params[0]] ?? null;
        if($tx === null) return null;
        if($method === 'eth_getTransactionReceipt') return ['status' => $tx['status'] ?? '0x1',
            'blockNumber' => '0x' . dechex($tx['block']), 'logs' => $tx['logs'] ?? []];
        if($method === 'eth_getTransactionByHash') return ['to' => $tx['to'] ?? DONATION_ADDRESS, 'value' => $tx['wei'] ?? '0x0'];
        throw new RuntimeException("unexpected $method");
    };
}

foreach([false, true] as $forcePython) {
    $db = donationDatabase(':memory:', $forcePython);
    $chain = [
        tx('1') => ['block' => 900, 'time' => NOW - 60, 'logs' => [usdc(50)]],
        tx('2') => ['block' => 900, 'time' => NOW - 60, 'wei' => '0x' . dechex(10 ** 16)],    // 0.01 ETH
        tx('3') => ['block' => 998, 'time' => NOW - 10, 'logs' => [usdc(20)]],                // 3 confirmations
        tx('4') => ['block' => 901, 'time' => DONATION_OPENED - 1, 'logs' => [usdc(100)]],
        tx('5') => ['block' => 900, 'time' => NOW - 60, 'logs' => [usdc(100, '0x' . str_repeat('0', 24) . str_repeat('1', 40))]],
        tx('6') => ['block' => 900, 'time' => NOW - 60, 'logs' => [usdc(100, OURS, '0x' . str_repeat('ab', 20))]],
        tx('7') => ['block' => 900, 'time' => NOW - 60, 'logs' => [usdc(100)], 'status' => '0x0'],
        tx('8') => ['block' => 900, 'time' => NOW - 60, 'logs' => [usdc(100)]],
        tx('9') => ['block' => 900, 'time' => NOW - 60, 'wei' => '0x' . dechex(10 ** 16), 'to' => '0x' . str_repeat('1', 40)],
    ];
    $issues = [];
    $github = function($method, $path, $payload) use (&$issues) {
        if($method === 'GET') return [200, $issues];
        $number = count($issues) + 1;
        $issues[] = ['body' => $payload['body'], 'title' => $payload['title'], 'labels' => $payload['labels'],
            'html_url' => "https://github.com/dunecity-project/dunecity/issues/$number"];
        return [201, end($issues)];
    };
    $io = ['now' => NOW, 'key' => 'k', 'rpc' => chain($chain), 'price' => fn($time) => 3000.0,
        'github' => $github, 'ipn' => fn($raw) => true];
    $member = ['kind' => 'member', 'tier' => 'silver', 'alias' => 'Muad\'Dib', 'github' => '', 'network' => 'base', 'tx' => tx('1')];

    // Paid USDC membership is listed immediately, without approval, and only once per transaction.
    $result = donationCrypto($member, $db, $io);
    check(($result['ok'] ?? false) && $result['until'] === gmdate('Y-m-d', NOW - 60 + DONATION_MEMBER_SECONDS), 'USDC membership failed');
    check(donationCrypto($member, $db, $io)['ok'] ?? false, 'same claim was not idempotent');
    $thief = $member; $thief['alias'] = 'Thief';
    check(str_contains(donationCrypto($thief, $db, $io)['error'] ?? '', 'already been used'), 'transaction reused by another claim');
    $list = donationSupporters($db, $io);
    check(array_column($list['members']['silver'], 'alias') === ["Muad'Dib"], 'member not listed');
    check(!str_contains(json_encode($list), tx('1')) && !str_contains(json_encode($list), '9999999999'), 'payment details published');

    // ETH is valued at the payment date: 0.01 ETH * $3000 = $30, so silver ($50) is refused and bronze accepted.
    $eth = ['kind' => 'member', 'tier' => 'silver', 'alias' => 'Chani', 'network' => 'ethereum', 'tx' => tx('2')];
    check(str_contains(donationCrypto($eth, $db, $io)['error'] ?? '', 'below the $50'), 'underpayment accepted');
    $eth['tier'] = 'bronze';
    check(donationCrypto($eth, $db, $io)['ok'] ?? false, 'ETH bronze refused after underpayment retry');

    check(str_contains(donationCrypto(['network' => 'base', 'tx' => tx('3')] + $member, $db, $io)['retry'] ?? '', '3 of 10'), 'unconfirmed accepted');
    check(isset(donationCrypto(['network' => 'base', 'tx' => tx('0')] + $member, $db, $io)['retry']), 'unknown tx not retried');
    check(str_contains(donationCrypto(['tx' => tx('4')] + $member, $db, $io)['error'] ?? '', 'before donations opened'), 'old wallet transfer claimable');
    foreach(['5', '6', '9'] as $digit)
        check(str_contains(donationCrypto(['tx' => tx($digit)] + $member, $db, $io)['error'] ?? '', 'did not send'), "foreign transfer $digit accepted");
    check(str_contains(donationCrypto(['tx' => tx('7')] + $member, $db, $io)['error'] ?? '', 'failed'), 'failed tx accepted');
    $broken = $io; $broken['rpc'] = fn() => throw new RuntimeException('down');
    check(isset(donationCrypto(['tx' => tx('8')] + $member, $db, $broken)['retry']), 'RPC outage not retryable');
    foreach([['alias' => 'x'], ['alias' => '<script>'], ['alias' => "a\tb"], ['tier' => 'platinum'], ['github' => 'bad_name'], ['tx' => '0x12']] as $bad)
        check(isset(donationCrypto($bad + $member, $db, $io)['error']), 'invalid request accepted: ' . json_encode($bad));

    // Paid feature creates exactly one labelled GitHub issue mentioning the sponsor.
    $feature = ['kind' => 'feature', 'tier' => 'large', 'alias' => 'Stilgar', 'github' => '@fremen-1', 'network' => 'base', 'tx' => tx('8'),
        'title' => 'Sandworm riding', 'details' => "Let infantry ride worms.\nThey should steer them."];
    $result = donationCrypto($feature, $db, $io);
    check(($result['issue'] ?? '') === 'https://github.com/ggtothemax/dunecity/issues/1', 'feature issue missing');
    check($issues[0]['labels'] === ['enhancement', 'sponsored-feature', 'size: large'], 'labels wrong');
    check(str_contains($issues[0]['body'], 'Stilgar (@fremen-1)') && str_contains($issues[0]['body'], '100.00 USDC on Base'), 'issue body incomplete');
    check(donationCrypto($feature, $db, $io)['issue'] === $result['issue'] && count($issues) === 1, 'retry duplicated issue');
    check(donationSupporters($db, $io)['features'][0]['title'] === 'Sandworm riding', 'feature not listed');

    // GitHub outage: payment kept, issue created later by reconciliation, never duplicated.
    $chain[tx('a')] = ['block' => 900, 'time' => NOW - 60, 'logs' => [usdc(20)]];
    $offline = $io; $offline['github'] = fn() => [0, []];
    $small = ['tier' => 'small', 'tx' => tx('a'), 'title' => 'Bigger minimap'] + $feature;
    check(($result = donationCrypto($small, $db, $offline))['ok'] && $result['issue'] === '', 'feature lost during GitHub outage');
    $later = $io; $later['now'] = NOW + 601;
    donationSupporters($db, $later);
    check(count($issues) === 2 && str_contains($issues[1]['title'], '[Sponsored · Small] Bigger minimap'), 'reconcile did not create issue');
    $later['now'] = NOW + 1300;
    donationSupporters($db, $later);
    check(count($issues) === 2, 'reconcile duplicated issue');

    // Hidden and expired entries leave the public wall.
    $db->exec("UPDATE donations SET hidden=1 WHERE alias='Chani'");
    $expired = $io; $expired['now'] = NOW + DONATION_MEMBER_SECONDS;
    check(donationSupporters($db, $io)['members']['bronze'] === [], 'hidden alias listed');
    check(donationSupporters($db, $expired)['members']['silver'] === [], 'expired member listed');

    // PayPal: the server fixes the amount and recipient; only IPNs PayPal re-validates count.
    $verdict = true;
    $pp = $io; $pp['ipn'] = function($raw) use (&$verdict) { return $verdict; };
    $start = function(array $choice) use ($db, $pp): string {
        parse_str((string)parse_url(donationPaypalStart($choice, $db, $pp)['redirect'], PHP_URL_QUERY), $query);
        return $query['custom'];
    };
    $ipn = function(string $id, array $fields) use ($db, $pp): bool {
        $fields += ['txn_type' => 'web_accept', 'business' => 'Donations@dunelegacy.com', 'receiver_email' => 'owner@example.com',
            'mc_currency' => 'USD', 'payment_status' => 'Completed', 'custom' => $id];
        return donationPaypalIpn(http_build_query($fields), $fields, $db, $pp);
    };
    $gold = ['kind' => 'member', 'tier' => 'gold', 'alias' => 'Leto'];
    parse_str((string)parse_url(donationPaypalStart(['amount' => '1'] + $gold, $db, $pp)['redirect'], PHP_URL_QUERY), $query);
    check($query['amount'] === '100.00' && $query['currency_code'] === 'USD' && $query['business'] === 'donations@dunelegacy.com'
        && str_ends_with($query['notify_url'], '/metaserver/donations.php?action=paypal-ipn'), 'client influenced PayPal payment');
    $leto = $query['custom'];
    check(isset(donationPaypalStatus(['id' => $leto], $db, $pp)['waiting']), 'unpaid PayPal choice confirmed');
    $verdict = false;
    check($ipn($leto, ['txn_id' => 'FORGED00000000001', 'mc_gross' => '100.00']), 'forged IPN not acknowledged');
    check(isset(donationPaypalStatus(['id' => $leto], $db, $pp)['waiting']), 'forged IPN accepted');
    $verdict = null;
    check(!$ipn($leto, ['txn_id' => 'TXN00000000000001', 'mc_gross' => '100.00']), 'unverifiable IPN not retried');
    $verdict = true;
    check($ipn($leto, ['txn_id' => 'TXN00000000000001', 'mc_gross' => '100.00']), 'valid IPN not acknowledged');
    check(donationPaypalStatus(['id' => $leto], $db, $pp)['ok'] ?? false, 'PayPal payment not confirmed');
    check($ipn($leto, ['txn_id' => 'TXN00000000000001', 'mc_gross' => '100.00']), 'duplicate IPN failed');
    check(array_column(donationSupporters($db, $pp)['members']['gold'], 'alias') === ['Leto'], 'PayPal member not listed');
    check(isset(donationPaypalStatus(['id' => str_repeat('0', 32)], $db, $pp)['error']), 'unknown PayPal payment accepted');

    $rejected = [['mc_gross' => '1.00'], ['mc_currency' => 'AUD'], ['business' => 'someone@example.com'],
        ['payment_status' => 'Denied'], ['txn_id' => 'TXN00000000000001']];
    foreach($rejected as $n => $bad) {
        $id = $start(['alias' => "Feyd $n"] + $gold);
        $ipn($id, $bad + ['txn_id' => 'TXNBAD00000000' . $n, 'mc_gross' => '100.00']);
        check(isset(donationPaypalStatus(['id' => $id], $db, $pp)['waiting']), 'mismatched IPN accepted: ' . json_encode($bad));
    }

    // Pending clears later; refunds take the alias off the wall.
    $jessica = $start(['alias' => 'Jessica'] + $gold);
    $ipn($jessica, ['txn_id' => 'TXN00000000000002', 'mc_gross' => '100.00', 'payment_status' => 'Pending']);
    check(isset(donationPaypalStatus(['id' => $jessica], $db, $pp)['pending']), 'pending PayPal payment not reported');
    $ipn($jessica, ['txn_id' => 'TXN00000000000002', 'mc_gross' => '100.00']);
    check(donationPaypalStatus(['id' => $jessica], $db, $pp)['ok'] ?? false, 'cleared PayPal payment not confirmed');
    $ipn($leto, ['txn_id' => 'TXN00000000000003', 'parent_txn_id' => 'TXN00000000000001', 'mc_gross' => '-100.00',
        'payment_status' => 'Refunded']);
    check(array_column(donationSupporters($db, $pp)['members']['gold'], 'alias') === ['Jessica'], 'refunded member still listed');
    check(isset(donationPaypalStatus(['id' => $leto], $db, $pp)['error']), 'refunded payment still confirmed');

    // A PayPal feature opens its issue from the IPN, before the donor returns.
    $feature = $start(['tier' => 'medium', 'alias' => 'Duncan', 'title' => 'Spice storms'] + $small);
    $ipn($feature, ['txn_id' => 'TXN00000000000004', 'mc_gross' => '50.00']);
    check(count($issues) === 3 && str_contains($issues[2]['body'], '50.00 USD via PayPal'), 'PayPal feature issue missing');
    check(str_ends_with(donationPaypalStatus(['id' => $feature], $db, $pp)['issue'] ?? '', '/issues/3'), 'PayPal feature issue not returned');

    // Unpaid choices are deleted after two days.
    $abandoned = $start(['alias' => 'Abandoned'] + $gold);
    $later = $pp; $later['now'] = NOW + 2 * 86400 + 1;
    donationSupporters($db, $later);
    check(isset(donationPaypalStatus(['id' => $abandoned], $db, $pp)['error']), 'unpaid choice kept');
    check(donationPaypalStatus(['id' => $jessica], $db, $pp)['ok'] ?? false, 'paid PayPal donation deleted');

    check(donationAdmit($db, '1.2.3.4', 'k', NOW), 'first attempt refused');
    for($i = 1; $i < 30; ++$i) donationAdmit($db, '1.2.3.4', 'k', NOW);
    check(!donationAdmit($db, '1.2.3.4', 'k', NOW) && donationAdmit($db, '5.6.7.8', 'k', NOW), 'rate limit wrong');
    check(donationAdmit($db, '1.2.3.4', 'k', NOW + 3601), 'rate limit did not expire');
    echo ($forcePython ? 'Python' : 'PDO') . " donation storage passed.\n";
}
