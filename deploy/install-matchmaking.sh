#!/usr/bin/env bash
# Run as administrator with a separately verified archive of committed source.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Administrator access required' >&2; exit 1; }
BUNDLE=${1:?bundle path}; EXPECTED=${2:?archive SHA256}; REVISION=${3:?source commit}
[[ $EXPECTED =~ ^[0-9a-f]{64}$ && $REVISION =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ -f $BUNDLE && ! -L $BUNDLE ]] || exit 1
printf '%s  %s\n' "$EXPECTED" "$BUNDLE" | sha256sum --check --status
BASE=/opt/dunecity-matchmaking
install -d -m 0755 "$BASE/releases" /etc/apache2/snippets
exec 9>/root/.dunecity-matchmaking-install.lock
flock -n 9
NODE_VERSION=22.23.2
NODE_HASH=d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307
[[ $(uname -m) == x86_64 ]] || { echo 'Installer currently targets Linux x64' >&2; exit 1; }
if [[ ! -d $BASE/node ]]; then
  RUNTIME_STAGE=$(mktemp -d "$BASE/.node.XXXXXX")
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" -o "$RUNTIME_STAGE/node.tar.xz"
  printf '%s  %s\n' "$NODE_HASH" "$RUNTIME_STAGE/node.tar.xz" | sha256sum --check --status
  tar -xJf "$RUNTIME_STAGE/node.tar.xz" -C "$RUNTIME_STAGE"
  mv "$RUNTIME_STAGE/node-v$NODE_VERSION-linux-x64" "$BASE/node"
  rm "$RUNTIME_STAGE/node.tar.xz"; rmdir "$RUNTIME_STAGE"
fi
[[ $("$BASE/node/bin/node" --version) == v$NODE_VERSION ]] || exit 1
RELEASE="$BASE/releases/$REVISION"
if [[ ! -d $RELEASE ]]; then
  STAGE=$(mktemp -d "$BASE/.source.XXXXXX")
  tar -xzf "$BUNDLE" -C "$STAGE"
  PATH="$BASE/node/bin:$PATH" "$BASE/node/bin/node" "$BASE/node/lib/node_modules/npm/bin/npm-cli.js" \
    ci --prefix "$STAGE/matchmaking-service" --omit=dev --ignore-scripts --no-audit --no-fund
  "$BASE/node/bin/node" --test "$STAGE/matchmaking-service/test/service.test.js"
  printf '%s\n' "$REVISION" > "$STAGE/matchmaking-service/REVISION"
  chown -R root:root "$STAGE"; chmod -R go-w "$STAGE"
  mv "$STAGE" "$RELEASE"
fi
# Retain every prior release for rollback. The live service reads root-owned files.
ln -s "$RELEASE/matchmaking-service" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"
install -m 0644 "$RELEASE/deploy/dunecity-matchmaking.service" /etc/systemd/system/dunecity-matchmaking.service
systemd-analyze verify /etc/systemd/system/dunecity-matchmaking.service
systemctl daemon-reload
systemctl enable --now dunecity-matchmaking.service
systemctl restart dunecity-matchmaking.service
for attempt in $(seq 1 20); do
  if curl --fail --silent http://127.0.0.1:8788/health | python3 -c 'import json,sys;assert json.load(sys.stdin)["status"]=="ok"' 2>/dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8788/health
VHOST=/etc/apache2/sites-available/dunelegacy-le-ssl.conf
BACKUP="$VHOST.pre-matchmaking.$(date +%s)"
cp -a "$VHOST" "$BACKUP"
install -m 0644 "$RELEASE/deploy/dunecity-matchmaking-apache.conf" /etc/apache2/snippets/dunecity-matchmaking.conf
a2enmod proxy proxy_http proxy_wstunnel rewrite >/dev/null
python3 - "$VHOST" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text();directive='    Include /etc/apache2/snippets/dunecity-matchmaking.conf\n'
if directive.strip() not in s:
    if s.count('</VirtualHost>')!=1: raise SystemExit('Expected one TLS virtual host')
    p.write_text(s.replace('</VirtualHost>',directive+'</VirtualHost>'))
PY
if ! apache2ctl configtest; then cp -a "$BACKUP" "$VHOST"; exit 1; fi
systemctl reload apache2
systemctl is-active dunecity-matchmaking.service
printf '\nInstalled matchmaking source %s; vhost backup %s\n' "$REVISION" "$BACKUP"
