// Support page: supporter wall, PayPal checkout and crypto confirmation.
// The server verifies every payment; nothing here decides what was paid.
(() => {
    'use strict';
    const api = '/metaserver/donations.php';
    const byId = (id) => document.getElementById(id);
    const form = byId('donate-form');
    const tierNames = { bronze: 'Bronze', silver: 'Silver', gold: 'Gold', small: 'Small', medium: 'Medium', large: 'Large' };
    let tiers = { member: { bronze: 20, silver: 50, gold: 100 }, feature: { small: 20, medium: 50, large: 100 } };

    const choice = () => {
        const [kind, tier] = form.querySelector('input[name="choice"]:checked').value.split(':');
        return { kind, tier };
    };

    function updateChoice() {
        const { kind, tier } = choice();
        const feature = kind === 'feature';
        byId('feature-fields').hidden = !feature;
        byId('feature-title').required = feature;
        byId('feature-details').required = feature;
        byId('amount').textContent = `$${tiers[kind][tier]} USD`;
    }

    function fields() {
        const { kind, tier } = choice();
        const data = new URLSearchParams({ kind, tier, alias: byId('alias').value, github: byId('github').value });
        if (kind === 'feature') {
            data.set('title', byId('feature-title').value);
            data.set('details', byId('feature-details').value);
        }
        return data;
    }

    async function post(action, data) {
        try {
            const response = await fetch(`${api}?action=${action}`, { method: 'POST', body: data });
            return await response.json();
        } catch {
            return { retry: 'Could not reach dunelegacy.com…' };
        }
    }

    function setStatus(element, text, kind = '') {
        element.textContent = text || '';
        element.className = `pay-status ${kind}`;
    }

    function showResult(result) {
        const box = byId('donate-result');
        box.replaceChildren();
        const heading = document.createElement('h3');
        const text = document.createElement('p');
        heading.textContent = `Thank you, ${result.alias}!`;
        if (result.kind === 'member') {
            text.textContent = `You are on the supporters wall as ${tierNames[result.tier]} until ${result.until}.`;
        } else if (result.issue) {
            text.append('Your sponsored feature is now ');
            const link = document.createElement('a');
            link.href = result.issue;
            link.textContent = 'a GitHub issue';
            text.append(link, '. We will follow up there to agree the details.');
        } else {
            text.textContent = 'Your payment is confirmed. The GitHub issue for your feature will be created within a few minutes and listed below.';
        }
        box.append(heading, text);
        box.hidden = false;
        form.hidden = true;
        box.scrollIntoView({ behavior: 'smooth', block: 'center' });
        loadSupporters();
    }

    function handle(result, status) {
        if (result.ok) showResult(result);
        else if (result.pending) setStatus(status, result.pending, 'waiting');
        else setStatus(status, result.error || result.retry || 'Something went wrong. Please try again.', 'error');
    }

    // Crypto: the server reads the transaction from the chain; keep polling until it is confirmed.
    let polling = null;
    async function verifyCrypto(attempt = 0) {
        const status = byId('crypto-status');
        clearTimeout(polling);
        if (!form.reportValidity()) return;
        const tx = byId('tx').value.trim();
        if (!/^0x[0-9a-fA-F]{64}$/.test(tx)) {
            setStatus(status, 'Paste the transaction hash: 0x followed by 64 characters.', 'error');
            return;
        }
        setStatus(status, 'Checking the transaction…', 'waiting');
        const data = fields();
        data.set('network', byId('network').value);
        data.set('tx', tx);
        const result = await post('crypto', data);
        if (result.retry && attempt < 40) {
            setStatus(status, `${result.retry} Checking again in 15 seconds.`, 'waiting');
            polling = setTimeout(() => verifyCrypto(attempt + 1), 15000);
            return;
        }
        handle(result, status);
    }

    // PayPal: the server records the choice and sends the donor to PayPal. PayPal's payment
    // notification, not the return to this page, is what confirms the payment.
    async function payWithPaypal() {
        const status = byId('paypal-status');
        if (!form.reportValidity()) return;
        setStatus(status, 'Opening PayPal…', 'waiting');
        const result = await post('paypal-start', fields());
        if (result.redirect) window.location.assign(result.redirect);
        else handle(result, status);
    }

    async function confirmPaypal(id, attempt = 0) {
        const status = byId('paypal-status');
        const result = await post('paypal-status', new URLSearchParams({ id }));
        if ((result.waiting || result.pending || result.retry) && attempt < 36) {
            setStatus(status, result.waiting || result.pending || result.retry, 'waiting');
            setTimeout(() => confirmPaypal(id, attempt + 1), 5000);
            return;
        }
        if (result.waiting) result.error = 'PayPal has not confirmed this payment yet. Your entry appears automatically once it does.';
        handle(result, status);
    }

    function renderWall(data) {
        for (const tier of ['gold', 'silver', 'bronze']) {
            const list = byId(`wall-${tier}`);
            const members = data.members[tier] || [];
            list.replaceChildren(...members.map((member) => {
                const item = document.createElement('li');
                item.textContent = member.alias;
                item.title = `Supporter since ${member.since}`;
                return item;
            }));
            if (!members.length) {
                const empty = document.createElement('li');
                empty.className = 'empty';
                empty.textContent = 'Be the first';
                list.append(empty);
            }
        }
        const features = byId('sponsored-features');
        features.replaceChildren(...data.features.map((feature) => {
            const item = document.createElement('li');
            const title = /^https:\/\/github\.com\//.test(feature.issue) ? document.createElement('a') : document.createElement('strong');
            if (title.tagName === 'A') title.href = feature.issue;
            title.textContent = feature.title;
            item.append(title, ` · ${tierNames[feature.size]} · sponsored by ${feature.alias} · ${feature.date}`);
            return item;
        }));
        if (!data.features.length) {
            const empty = document.createElement('li');
            empty.className = 'empty';
            empty.textContent = 'No sponsored features yet.';
            features.append(empty);
        }
    }

    async function loadSupporters() {
        try {
            const response = await fetch(`${api}?action=supporters`);
            const data = await response.json();
            if (!data.members) throw new Error('supporters');
            tiers = data.tiers || tiers;
            renderWall(data);
            updateChoice();
        } catch {
            setStatus(byId('paypal-status'), 'Donations are temporarily unavailable.', 'error');
        }
    }

    form.addEventListener('change', (event) => { if (event.target.name === 'choice') updateChoice(); });
    form.addEventListener('submit', (event) => event.preventDefault());
    byId('verify-crypto').addEventListener('click', () => verifyCrypto());
    byId('paypal-pay').addEventListener('click', () => payWithPaypal());
    byId('copy-address').addEventListener('click', async (event) => {
        await navigator.clipboard.writeText(byId('wallet-address').textContent.trim());
        event.target.textContent = 'Copied';
    });
    updateChoice();
    loadSupporters();
    const returned = new URLSearchParams(window.location.search).get('paypal');
    if (returned && /^[0-9a-f]{32}$/.test(returned)) {
        byId('donate').scrollIntoView({ block: 'start' });
        confirmPaypal(returned);
    }
})();
