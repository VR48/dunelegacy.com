#!/usr/bin/env bash
set -euo pipefail

DEPLOY_USER="${1:-dunelegacy-deploy}"
PUBLIC_KEY_FILE="${2:-/root/dunelegacy-deploy.pub}"
REPOSITORY_URL="https://github.com/VR48/dunelegacy.com.git"
REPOSITORY_ROOT="/srv/dunelegacy-deploy/repo"
WEB_ROOT="/var/www/html"
DATA_ROOT="/var/www/data"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This bootstrap must run as root." >&2
    exit 1
fi
if ! ssh-keygen -l -f "$PUBLIC_KEY_FILE" >/dev/null 2>&1; then
    echo "The deployment public key is invalid." >&2
    exit 1
fi

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod --append --groups www-data "$DEPLOY_USER"

DEPLOY_HOME="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_HOME/.ssh"
DEPLOY_AUTHORIZED_KEYS="$(mktemp)"
printf 'restrict %s\n' "$(cat "$PUBLIC_KEY_FILE")" > "$DEPLOY_AUTHORIZED_KEYS"
install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
    "$DEPLOY_AUTHORIZED_KEYS" "$DEPLOY_HOME/.ssh/authorized_keys"
rm -f "$DEPLOY_AUTHORIZED_KEYS"

if ! command -v rsync >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y rsync
fi

install -d -m 0755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$(dirname "$REPOSITORY_ROOT")"
if [[ ! -d "$REPOSITORY_ROOT/.git" ]]; then
    runuser -u "$DEPLOY_USER" -- git clone --filter=blob:none "$REPOSITORY_URL" "$REPOSITORY_ROOT"
else
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "$REPOSITORY_ROOT"
fi

install -d -m 0755 -o "$DEPLOY_USER" -g www-data "$WEB_ROOT"
chown -R "$DEPLOY_USER:www-data" "$WEB_ROOT"
rm -rf "$WEB_ROOT/.git"

install -d -m 0750 -o www-data -g www-data "$DATA_ROOT"
touch "$DATA_ROOT/discord_webhook.txt"
chown "$DEPLOY_USER:www-data" "$DATA_ROOT/discord_webhook.txt"
chmod 0640 "$DATA_ROOT/discord_webhook.txt"

a2enmod headers rewrite deflate >/dev/null
cat > /etc/apache2/conf-available/zz-dunelegacy-security.conf <<'EOF'
ServerTokens Prod
ServerSignature Off
TraceEnable Off

<Directory /var/www/html>
    Options -Indexes +FollowSymLinks
    AllowOverride FileInfo Options=Indexes
    Require all granted
</Directory>
EOF
a2disconf dunelegacy-security >/dev/null 2>&1 || true
rm -f /etc/apache2/conf-available/dunelegacy-security.conf
a2enconf zz-dunelegacy-security >/dev/null

if [[ -f /etc/apache2/sites-available/dunelegacy.conf ]]; then
    sed -i 's/^[[:space:]]*AllowOverride All[[:space:]]*$/            AllowOverride FileInfo Options=Indexes/' \
        /etc/apache2/sites-available/dunelegacy.conf
fi
while IFS= read -r php_conf; do
    printf '%s\n' 'expose_php = Off' > "$php_conf/99-dunelegacy-security.ini"
done < <(find /etc/php -type d -path '*/apache2/conf.d' 2>/dev/null)

apache2ctl configtest
systemctl reload apache2

if command -v ufw >/dev/null 2>&1; then
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow OpenSSH
    ufw allow 'Apache Full'
    ufw --force enable
fi

ROOT_KEYS=/root/.ssh/authorized_keys
if [[ -f "$ROOT_KEYS" ]]; then
    key_material="$(awk 'NR == 1 { print $1 " " $2 }' "$PUBLIC_KEY_FILE")"
    cp -a "$ROOT_KEYS" "$ROOT_KEYS.pre-dunelegacy-hardening"
    awk -v key="$key_material" 'index($0, key) == 0' "$ROOT_KEYS" > "$ROOT_KEYS.tmp"
    chmod 0600 "$ROOT_KEYS.tmp"
    mv "$ROOT_KEYS.tmp" "$ROOT_KEYS"
fi

rm -f "$PUBLIC_KEY_FILE"
echo "Secure deployment account configured: $DEPLOY_USER"
