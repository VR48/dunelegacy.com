#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="https://github.com/VR48/dunelegacy.com.git"
REPOSITORY_ROOT="${REPOSITORY_ROOT:-/srv/dunelegacy-deploy/repo}"
WEB_ROOT="${WEB_ROOT:-/var/www/html}"
STAGING_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGING_ROOT"' EXIT

if [[ ! -d "$REPOSITORY_ROOT/.git" ]]; then
    git clone --filter=blob:none "$REPOSITORY_URL" "$REPOSITORY_ROOT"
fi

git -C "$REPOSITORY_ROOT" remote set-url origin "$REPOSITORY_URL"
git -C "$REPOSITORY_ROOT" fetch --quiet --no-tags origin main
git -C "$REPOSITORY_ROOT" archive origin/main website \
    | tar -x -C "$STAGING_ROOT" --strip-components=1
git -C "$REPOSITORY_ROOT" archive origin/main metaserver \
    | tar -x -C "$STAGING_ROOT"

python3 "$REPOSITORY_ROOT/deploy/check-web-security.py"
rsync -a --delete --exclude='.well-known/' "$STAGING_ROOT/" "$WEB_ROOT/"

find "$WEB_ROOT" -type d -exec chmod 0755 {} +
find "$WEB_ROOT" -type f -exec chmod 0644 {} +
echo "Deployed $(git -C "$REPOSITORY_ROOT" rev-parse --short origin/main)"
