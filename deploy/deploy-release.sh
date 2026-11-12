#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="https://github.com/VR48/dunelegacy.com.git"
REPOSITORY_ROOT="${REPOSITORY_ROOT:-/srv/dunelegacy-deploy/repo}"
WEB_ROOT="${WEB_ROOT:-/var/www/html}"
: "${DEPLOY_REVISION:?Set the exact full website source SHA; retries must reuse it}"
DEPLOY_CHANNEL="${DEPLOY_CHANNEL:-site}"
[[ "$DEPLOY_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full website SHA' >&2; exit 1; }
[[ "$DEPLOY_CHANNEL" = site || "$DEPLOY_CHANNEL" = staging ]] || { echo 'Expected site or staging channel' >&2; exit 1; }
STAGING_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGING_ROOT"' EXIT
# Serialize manual recovery with the workflow's deployment, not just GitHub runs.
exec 9> "$HOME/dunelegacy-deployment.lock"
flock 9
if [[ ! -d "$REPOSITORY_ROOT/.git" ]]; then
    git clone --filter=blob:none "$REPOSITORY_URL" "$REPOSITORY_ROOT"
fi
git -C "$REPOSITORY_ROOT" remote set-url origin "$REPOSITORY_URL"
git -C "$REPOSITORY_ROOT" fetch --quiet --no-tags origin main "$DEPLOY_REVISION"
git -C "$REPOSITORY_ROOT" merge-base --is-ancestor "$DEPLOY_REVISION" origin/main || {
    echo 'Refusing website source outside trusted main history' >&2; exit 1;
}
if [[ -f "$HOME/dunelegacy-last-deployed" ]]; then
    PREVIOUS="$(cat "$HOME/dunelegacy-last-deployed")"
    if [[ "$PREVIOUS" != "$DEPLOY_REVISION" ]] && git -C "$REPOSITORY_ROOT" merge-base --is-ancestor "$DEPLOY_REVISION" "$PREVIOUS"; then
        echo "Refusing stale website revision $DEPLOY_REVISION; $PREVIOUS is already deployed" >&2
        exit 1
    fi
fi
# The same immutable revision supplies source, validation, services and manifests.
# Never fetch origin/main again between compression, transfer and installation.
git -C "$REPOSITORY_ROOT" archive "$DEPLOY_REVISION" website metaserver p2p-service deploy \
    | tar -x -C "$STAGING_ROOT"
PUBLIC_ROOT="$STAGING_ROOT/website"
CHANNELS=(play)
if [[ "$DEPLOY_CHANNEL" = staging ]]; then
    test -d "$PUBLIC_ROOT/play-staging"
    CHANNELS=(play-staging)
elif [[ -d "$PUBLIC_ROOT/play-staging" ]]; then
    CHANNELS+=(play-staging)
fi
if [[ "$DEPLOY_CHANNEL" = site ]]; then
    python3 - "$PUBLIC_ROOT/play/build.json" "$WEB_ROOT/play/build.json" <<'PY'
import json, pathlib, sys
desired = json.loads(pathlib.Path(sys.argv[1]).read_text())
live_path = pathlib.Path(sys.argv[2])
if live_path.exists():
    live = json.loads(live_path.read_text())
    if tuple(map(int, desired['version'].split('.'))) < tuple(map(int, live['version'].split('.'))):
        raise SystemExit('Frozen site recovery would downgrade the live stable browser')
    if desired['version'] == live['version'] and desired['sourceCommit'] != live['sourceCommit']:
        raise SystemExit('Stable browser source differs at the same published version; use a new release')
PY
fi
for CHANNEL in "${CHANNELS[@]}"; do
    python3 "$STAGING_ROOT/deploy/check-web-security.py" --play "$PUBLIC_ROOT/$CHANNEL"
    python3 "$STAGING_ROOT/deploy/play_parts.py" "$PUBLIC_ROOT/$CHANNEL"
    if [[ -n "${PLAY_TRANSFER_ROOT:-}" ]]; then
        python3 "$STAGING_ROOT/deploy/install-play-transfer.py" \
            --source "$PLAY_TRANSFER_ROOT/$CHANNEL" --play "$PUBLIC_ROOT/$CHANNEL" \
            --revision "$DEPLOY_REVISION"
    fi
done
# Content comparison preserves cache validators for unchanged bytes. Stable
# website snapshots predating staging must not --delete a live staging channel.
WEB_SYNC_ARGS=(-a --checksum --no-times --delete --exclude='.well-known/' --exclude='/play-staging/')
if [[ "$DEPLOY_CHANNEL" = staging ]]; then
    install -d -m 0755 "$WEB_ROOT/play-staging"
    rsync "${WEB_SYNC_ARGS[@]}" "$PUBLIC_ROOT/play-staging/" "$WEB_ROOT/play-staging/"
    PERMISSION_ROOT="$WEB_ROOT/play-staging"
else
    python3 "$STAGING_ROOT/deploy/install-p2p-service.py" \
        --source "$STAGING_ROOT/p2p-service" --group www-data
    mv "$STAGING_ROOT/metaserver" "$PUBLIC_ROOT/metaserver"
    rsync "${WEB_SYNC_ARGS[@]}" "$PUBLIC_ROOT/" "$WEB_ROOT/"
    PERMISSION_ROOT="$WEB_ROOT"
    install -d -m 0755 "$HOME/bin"
    git -C "$REPOSITORY_ROOT" show "$DEPLOY_REVISION:deploy/query-analytics.py" > "$HOME/bin/metaserver-sqlite"
    if [[ -d "$PUBLIC_ROOT/play-staging" ]]; then
        install -d -m 0755 "$WEB_ROOT/play-staging"
        rsync "${WEB_SYNC_ARGS[@]}" "$PUBLIC_ROOT/play-staging/" "$WEB_ROOT/play-staging/"
    fi
    chmod 0755 "$HOME/bin/metaserver-sqlite"
fi
find "$PERMISSION_ROOT" -type d -exec chmod 0755 {} +
find "$PERMISSION_ROOT" -type f -exec chmod 0644 {} +
printf '%s\n' "$DEPLOY_REVISION" > "$HOME/dunelegacy-last-deployed"
printf 'sourceCommit=%s\nchannel=%s\nrunId=%s\n' "$DEPLOY_REVISION" "$DEPLOY_CHANNEL" "${DEPLOY_RUN_ID:-manual}" \
    > "$HOME/dunelegacy-deployment-${DEPLOY_REVISION}.txt"
echo "Deployed frozen website $DEPLOY_REVISION ($DEPLOY_CHANNEL)"
