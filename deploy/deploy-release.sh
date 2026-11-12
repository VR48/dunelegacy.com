#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="https://github.com/VR48/dunelegacy.com.git"
REPOSITORY_ROOT="${REPOSITORY_ROOT:-/srv/dunelegacy-deploy/repo}"
WEB_ROOT="${WEB_ROOT:-/var/www/html}"
: "${DEPLOY_REVISION:?Set the exact full website source SHA; retries must reuse it}"
DEPLOY_CHANNEL="${DEPLOY_CHANNEL:-site}"
[[ "$DEPLOY_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full website SHA' >&2; exit 1; }
[[ "$DEPLOY_CHANNEL" = site || "$DEPLOY_CHANNEL" = staging ]] || { echo 'Expected site or staging channel' >&2; exit 1; }
LAST_DEPLOYED="$HOME/dunelegacy-last-deployed-${DEPLOY_CHANNEL}"
STAGING_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGING_ROOT"' EXIT
# Serialize manual recovery with the workflow's deployment, not just GitHub runs.
exec 9> "$HOME/dunelegacy-deployment.lock"
flock -n 9 || { echo 'Another frozen website deployment owns the lane' >&2; exit 3; }
if [[ ! -d "$REPOSITORY_ROOT/.git" ]]; then
    git clone --filter=blob:none "$REPOSITORY_URL" "$REPOSITORY_ROOT"
fi
git -C "$REPOSITORY_ROOT" remote set-url origin "$REPOSITORY_URL"
git -C "$REPOSITORY_ROOT" fetch --quiet --no-tags origin main "$DEPLOY_REVISION"
git -C "$REPOSITORY_ROOT" merge-base --is-ancestor "$DEPLOY_REVISION" origin/main || {
    echo 'Refusing website source outside trusted main history' >&2; exit 1;
}
if [[ -f "$LAST_DEPLOYED" ]]; then
    PREVIOUS="$(cat "$LAST_DEPLOYED")"
    if [[ "$PREVIOUS" != "$DEPLOY_REVISION" ]] && git -C "$REPOSITORY_ROOT" merge-base --is-ancestor "$DEPLOY_REVISION" "$PREVIOUS"; then
        echo "Refusing stale $DEPLOY_CHANNEL revision $DEPLOY_REVISION; $PREVIOUS is already deployed" >&2
        exit 1
    fi
fi
# The same immutable revision supplies source, validation, services and manifests.
# Never fetch origin/main again between compression, transfer and installation.
if [[ "$DEPLOY_CHANNEL" = staging ]]; then
    : "${STAGING_INPUT_ROOT:?Set the verified frozen staging artifact directory}"
    git -C "$REPOSITORY_ROOT" archive "$DEPLOY_REVISION" deploy website/.htaccess | tar -x -C "$STAGING_ROOT"
    mkdir -p "$STAGING_ROOT/website"
    python3 "$STAGING_ROOT/deploy/install-staging-artifact.py" prepare \
        --inputs "$STAGING_INPUT_ROOT" --repository "$STAGING_ROOT" \
        --website "$STAGING_ROOT/website" --website-revision "$DEPLOY_REVISION"
    python3 "$STAGING_ROOT/deploy/install-staging-artifact.py" install-service \
        --source "$STAGING_ROOT/p2p-staging" --repository "$STAGING_ROOT" --group www-data
    CHANNELS=(play-staging)
else
    git -C "$REPOSITORY_ROOT" archive "$DEPLOY_REVISION" website metaserver p2p-service deploy \
        | tar -x -C "$STAGING_ROOT"
    if [[ -f "$STAGING_ROOT/deploy/release-preview.json" ]]; then
        git -C "$REPOSITORY_ROOT" archive "$DEPLOY_REVISION" p2p-preview | tar -x -C "$STAGING_ROOT"
        python3 "$STAGING_ROOT/deploy/install-release-preview.py" \
            --repository "$STAGING_ROOT" --website "$STAGING_ROOT/website" --group www-data
    fi
    CHANNELS=(play)
fi
PUBLIC_ROOT="$STAGING_ROOT/website"
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
    if [[ "$CHANNEL" = play ]]; then
        python3 "$STAGING_ROOT/deploy/check-web-security.py" --play-root "$PUBLIC_ROOT/$CHANNEL"
    fi
    python3 "$STAGING_ROOT/deploy/play_parts.py" "$PUBLIC_ROOT/$CHANNEL"
    if [[ -n "${PLAY_TRANSFER_ROOT:-}" ]]; then
        TRANSFER_ARGS=()
        # Staging bytes are deliberately absent from Git; no raw-Git engine URL.
        if [[ "$CHANNEL" = play ]]; then TRANSFER_ARGS=(--revision "$DEPLOY_REVISION"); fi
        python3 "$STAGING_ROOT/deploy/install-play-transfer.py" \
            --source "$PLAY_TRANSFER_ROOT/$CHANNEL" --play "$PUBLIC_ROOT/$CHANNEL" "${TRANSFER_ARGS[@]}"
    fi
done
# Preserve independently published staging and every older immutable preview,
# including when a frozen site recovery predates their existence.
WEB_SYNC_ARGS=(-a --checksum --no-times --delete --exclude='.well-known/' --exclude='/play-staging/' --exclude='/play-test-*/')
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
    if [[ -f "$STAGING_ROOT/deploy/release-preview.json" ]]; then
        PREVIEW_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["path"])' "$STAGING_ROOT/deploy/release-preview.json")"
        if [[ -e "$WEB_ROOT/$PREVIEW_NAME" ]]; then
            cmp "$PUBLIC_ROOT/$PREVIEW_NAME/build.json" "$WEB_ROOT/$PREVIEW_NAME/build.json" || {
                echo 'Refusing to overwrite an immutable browser preview' >&2; exit 1;
            }
        else
            install -d -m 0755 "$WEB_ROOT/$PREVIEW_NAME"
            rsync "${WEB_SYNC_ARGS[@]}" "$PUBLIC_ROOT/$PREVIEW_NAME/" "$WEB_ROOT/$PREVIEW_NAME/"
        fi
    fi
    chmod 0755 "$HOME/bin/metaserver-sqlite"
fi
find "$PERMISSION_ROOT" -type d -exec chmod 0755 {} +
find "$PERMISSION_ROOT" -type f -exec chmod 0644 {} +
if [[ "$DEPLOY_CHANNEL" = staging ]]; then
    cp "$STAGING_INPUT_ROOT/staging-browser.json" "$HOME/dunelegacy-staging-publication.json"
fi
printf '%s\n' "$DEPLOY_REVISION" > "$LAST_DEPLOYED"
printf 'sourceCommit=%s\nchannel=%s\nrunId=%s\n' "$DEPLOY_REVISION" "$DEPLOY_CHANNEL" "${DEPLOY_RUN_ID:-manual}" \
    > "$HOME/dunelegacy-deployment-${DEPLOY_REVISION}.txt"
echo "Deployed frozen website $DEPLOY_REVISION ($DEPLOY_CHANNEL)"
