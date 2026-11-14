# Dune Legacy Website & Metaserver - Complete Guide

Everything you need to deploy and maintain the website and metaserver.

---

## 🚀 Quick Deploy (10 Minutes)

```bash
# 1. Create metaserver droplet
cd deploy && ./create-droplet.sh

# 2. Enable auto-deploy
./setup-github-actions.sh

# 3. Update DNS in GoDaddy with the droplet IP
# (Wait 10-15 minutes for DNS)

# 4. Enable SSL (REQUIRED for game!)
./setup-ssl.sh
```

**Done!** Website and metaserver auto-deploy on `git push`.

---

## What This Repo Is

- **Website:** Static HTML at https://dunelegacy.com
- **Metaserver:** PHP API at https://dunelegacy.com/metaserver/metaserver.php
- **Hosting:** Single DigitalOcean Droplet serves both
- **Auto-deploy:** Deploys automatically on `git push origin main`

**Cost:** $6/month total

---

## Architecture

```
Single Droplet ($6/month)
Ubuntu 24.04 + Apache + PHP
Auto-deploys via GitHub Actions (~20 sec)
         ↓
    dunelegacy.com
         ├── / → Static website
         └── /metaserver/ → PHP API
```

**How auto-deploy works:**
1. You push to `main` branch
2. GitHub Actions validates the browser policy and artifact hashes
3. SSH authenticates the droplet against a pinned host key
4. A restricted account stages both `website/` and `metaserver/`
5. The staged release is synchronized into the web root

**Directory structure on droplet:**
- `/var/www/html/` → Published files only; never a Git checkout
- `/srv/dunelegacy-deploy/repo/` → Restricted deployment checkout
- `/var/www/html/metaserver/` → Metaserver PHP files
- `/var/www/data/` → Persistent game statistics (NOT in git)

---

In-game anonymous GitHub issue submission is documented in [game feedback](docs/game-feedback.md), including the repo-scoped token and deployment secret.

## Immutable staging browser recovery

The game browser publisher writes only `deploy/staging-browser.json` to this
repository. Its push selects the isolated `staging` channel, freezes
`websiteCodeCommit`, and downloads the exact published browser and signaling
archives into temporary storage. It never replaces stable `/play` or commits
runtime archives into Git.

`publication-inputs.json` keeps the original package `runAttempt`, even when
publication alone is retried. `deploy/install-staging-artifact.py` requires all
seven package input jobs to succeed at that frozen attempt, independently of
the successful integration qualification. A failed `dev-release` may be recovered
only by a later completed successful attempt of the same producer run, source,
workflow, event and branch, with a successful `dev-release`. Absent input jobs on
that retry inherit the frozen successes; explicit failed/skipped retry inputs,
missing/duplicate frozen inputs, or changed producer identity are rejected.
Manifest/archive hashes, source identity and frozen website-code checks still
apply. Never rewrite a published manifest to name the retry attempt.

Preparation supports both the CI's separate output tree and the remote
`deploy-release.sh` layout: `--repository "$STAGING_ROOT" --website
"$STAGING_ROOT/website"`. In the latter, the frozen archive already supplies the
root `.htaccess`; preparation retains it without copying it onto itself.
Canonical website bases determine this in-place case, and direct policy-file
symlinks remain rejected.
The prepare CLI writes only its structured result to stdout (the CI saves this as
`staging-preparation.json`); security-validator diagnostics go to stderr.

Deploy a corrected website validator before requesting recovery, so the browser
request freezes the corrected website code. Then use the existing game workflow:

```sh
gh workflow run web.yml --repo dunecity-project/dunecity --ref main -f tag="$STAGING_TAG"
```

`STAGING_TAG` must name the already-published immutable snapshot. Follow both
the browser publisher and this repository's **Deploy to Droplet** staging run;
publication success alone does not prove deployment. The live
`/play-staging/build.json`, advertised hashes and actual browser menu must agree.
The game `latest-dev` known-good retention marker advances only after actual
qualification; a missing marker holds pruning, not browser delivery. Do not
replace its legacy assets or move stable tags as a recovery step.

Code-only recovery still needs a new metadata request after the corrected website
code is published: each request freezes `websiteCodeCommit`. For example, request
`e569f2bc956435185965a1a68e6dd6eec54c8c87` pins the failing
`5354f825d2bc61bf3a44f694385946edf940c6bc` helper; an unchanged deployment rerun
will execute that same code. Select the current qualified immutable staging tag
(never regress a newer delivered source), and let `web.yml` freeze a new request
using the same published archive bytes with corrected website code. Do not edit
the old request or replace its manifest/assets.


## Prerequisites

```bash
brew install doctl gh
doctl auth init
gh auth login
```

---

## Detailed Setup

### Step 1: Create Droplet (3 min)

```bash
cd deploy
./create-droplet.sh
```

**What it does:**
- Creates Ubuntu 24.04 VM
- Installs Apache + PHP
- Clones repo directly to `/var/www/html` (as a git repo for auto-deploy)
- Creates persistent data directory

**Save the IP address it gives you!**

### Step 2: Enable Auto-Deploy (2 min)

```bash
./setup-github-actions.sh
```

**Prompts:**
- Droplet IP (from step 1)
- SSH key (press 1 for default)

**What it does:**
- Pins the confirmed SSH host key in GitHub
- Adds the deployment key and droplet address as repository secrets
- Enables the one-time root-to-restricted-account migration

### Step 3: Enable Auto-Deploy (2 min)

```bash
./setup-github-actions.sh
```

**It will ask for:**
- Droplet IP (from step 2)
- SSH key (press 1 for default)

**This sets up:** Automatic deployment on every `git push`

---

### Step 4: Configure DNS (5 min wait)

**GoDaddy:**
1. Go to https://dcc.godaddy.com/domains
2. `dunelegacy.com` → DNS
3. Update/Add A records:
   ```
   Type: A
   Name: @
   Value: <YOUR_DROPLET_IP>
   TTL: 600
   
   Type: A
   Name: www
   Value: <YOUR_DROPLET_IP>
   TTL: 600
   ```

**This points BOTH dunelegacy.com and www.dunelegacy.com to your droplet.**

Wait 5-15 minutes for DNS propagation.

---

## Daily Usage

**Update website or metaserver:**
```bash
# Edit files
vim metaserver/metaserver.php

# Deploy
git commit -am "Update"
git push origin main

# ✅ Auto-deploys!
```

**Monitor deployments:**
- Website: https://cloud.digitalocean.com/apps
- Metaserver: https://github.com/svan058/dunelegacy.com/actions

---

## Data Persistence

**IMPORTANT:** Droplet has persistent data!

```
/var/www/html/              ← Code (updated by git pull) ✅ Safe to update
  ├── index.html            ← Website
  ├── *.css, *.js           ← Website assets
  └── metaserver/           ← Metaserver PHP files
      ├── metaserver.php
      ├── index.php
      └── download.php

/var/www/data/              ← Data (NEVER touched) ⚠️ PERSISTENT
  ├── servers.dat           ← Active game servers
  └── stats.json            ← Game statistics
```

**Deployments only update code, data stays forever.**

---

## Donations and sponsored features

`website/support.html` (script `website/js/support.js`) talks to
`metaserver/donations.php`. Payments are verified automatically; nothing waits
for manual approval.

- **Tiers:** supporter Bronze/Silver/Gold $20/$50/$100 per year (alias listed for
  365 days), sponsored feature Small/Medium/Large $20/$50/$100. Amounts live in
  `DONATION_TIERS` in `metaserver/donations_service.php`.
- **Crypto:** the donor pastes a transaction hash. The server reads it from a public
  RPC (Ethereum: ETH/USDC/USDT, 6 confirmations; Base: ETH/USDC, 10 confirmations),
  sums transfers to `ggtothemax.eth` (`0x03b4…3114`), values ETH at that day's
  Coinbase price and accepts 97% of the tier. Transactions before 2026-11-13 and
  reused hashes are refused.
- **PayPal:** PayPal Payments Standard to the account holding
  `donations@dunelegacy.com` (Stefan's personal account; the address forwards via
  Forward Email DNS). `paypal-start` records the choice and returns a PayPal URL with
  the fixed amount and the donation id in `custom`. PayPal posts an IPN to
  `donations.php?action=paypal-ipn`; the server re-validates it with PayPal
  (`cmd=_notify-validate`), then requires `business`/`receiver_email` to be that
  address, USD, and at least the tier amount. Refunds and reversals take the entry
  off the wall. No API credentials are needed. Unpaid choices are deleted after two days.
- **Features:** each paid feature opens one issue in `dunecity-project/dunecity` with
  labels `enhancement`, `sponsored-feature`, `size: <tier>`, using the feedback token
  (`/var/www/data/feedback_github_token.txt`). If GitHub is down the payment is kept
  and the issue is created on a later page load.
- **Data:** `/var/www/data/donations.sqlite`. No payer email, name or wallet address
  is stored. To remove an alias from the public page without losing the record:

```bash
ssh dunelegacy-deploy@<droplet> python3 - <<'PY'
import sqlite3
db = sqlite3.connect('/var/www/data/donations.sqlite')
db.execute("UPDATE donations SET hidden=1 WHERE alias=?", ('Alias to hide',))
db.commit()
PY
```

---

## Troubleshooting

### Website or Metaserver not responding

```bash
# Test website
curl https://dunelegacy.com

# Test metaserver
curl https://dunelegacy.com/metaserver/metaserver.php?action=list

# Inspect the deployment checkout
ssh dunelegacy-deploy@<DROPLET_IP>
git -C /srv/dunelegacy-deploy/repo status

# Apache service and logs still require an administrator or provider console
systemctl status apache2
tail -f /var/log/apache2/dunelegacy-error.log
```

### Auto-deploy failing

```bash
# Check GitHub Actions
gh run list

# Re-run setup
cd deploy && ./setup-github-actions.sh
```

### Deployment checkout failing on droplet

The published web root intentionally has no `.git` directory. Inspect the
restricted deployment checkout instead:

```bash
ssh dunelegacy-deploy@<DROPLET_IP>
git -C /srv/dunelegacy-deploy/repo status
git -C /srv/dunelegacy-deploy/repo fetch origin main
```

### DNS not resolving

```bash
dig metaserver.dunelegacy.com
# Wait 15 minutes for propagation
```

### Data not persisting

```bash
ssh root@<DROPLET_IP>
ls -la /var/www/data/
chown -R www-data:www-data /var/www/data
```

---

## File Structure

```
dunelegacy.com/
├── website/          # Static site files
├── metaserver/       # PHP metaserver
│   ├── metaserver.php   # Main API
│   ├── index.php        # Status page
│   └── download.php     # Downloads
├── deploy/           # Deployment scripts
│   ├── create-droplet.sh
│   └── setup-github-actions.sh
└── .github/workflows/   # Auto-deploy configs
```

---

## Advanced

### Finish Apache version suppression

The restricted deployment account cannot edit global Apache configuration.
After migrating an existing server, run this once from the DigitalOcean root
console, then confirm that `curl -I` reports `Server: Apache` without a version:

```bash
sed -i 's/^ServerTokens .*/ServerTokens Prod/' /etc/apache2/conf-available/security.conf
sed -i 's/^ServerSignature .*/ServerSignature Off/' /etc/apache2/conf-available/security.conf
apache2ctl configtest && systemctl reload apache2
curl -I https://dunelegacy.com/play/
```

### SSL Certificate Renewal

SSL certificates auto-renew via certbot. Check status:

```bash
ssh root@<DROPLET_IP>
systemctl status certbot.timer
certbot certificates
```

### Enable Backups

```bash
doctl compute droplet-action enable-backups <DROPLET_ID>
# +$1.20/month for weekly snapshots
```

### Manual Deploy

```bash
# Both website and metaserver
ssh dunelegacy-deploy@<DROPLET_IP> "bash /home/dunelegacy-deploy/deploy-release.sh"
```

---

## Safety Rules

✅ **Safe:** Update code via git push (data preserved)  
✅ **Safe:** Restart Apache (data preserved)  
✅ **Safe:** Reboot droplet (data preserved)  
❌ **NEVER:** Delete `/var/www/data/` (loses all game statistics!)  
❌ **NEVER:** Delete the droplet (loses everything!)  
❌ **NEVER:** Run `create-droplet.sh` twice (creates duplicate)

---

## Disaster Recovery

### Droplet failure

```bash
# Create new droplet
cd deploy && ./create-droplet.sh

# Update DNS to new IP
# Update GitHub secret
gh secret set METASERVER_DROPLET_IP --body "<NEW_IP>"

# Data lost unless you had backups enabled
```

**Recommendation:** Enable backups for $1.20/month

---

## Cost Breakdown

- Droplet (website + metaserver): $6/month
- Backups (optional): $1.20/month
- **Total: $6-7/month**

**Savings:** No App Platform cost! ✅

---

## Support

**Quick checks:**
```bash
curl -I https://dunelegacy.com
curl "https://dunelegacy.com/metaserver/metaserver.php?action=list"
gh run list
doctl compute droplet list
```

**View logs and stats:**
```bash
# SSH into droplet
ssh root@192.81.217.88

# View error logs
tail -50 /var/log/apache2/dunelegacy-error.log

# View access logs
tail -50 /var/log/apache2/dunelegacy-access.log

# View game statistics
cat /var/www/data/stats.json

# View active servers
ls -la /var/www/data/
```

**From your Mac (remote):**
```bash
ssh root@192.81.217.88 "cat /var/www/data/stats.json"
ssh root@192.81.217.88 "tail -20 /var/log/apache2/dunelegacy-error.log"
```

---

**Last Updated:** 2025-11-22

