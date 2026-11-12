# Direct-play signaling deployment

`website/p2p` is the public Apache entrypoint. `p2p-service` contains the matching
reviewed game service, copied by the game release workflow with its commit and
SHA-256 manifest. It is never copied into the public webroot.

The normal website deployment verifies and PHP-lints the service, installs it
under `/var/www/data/dunecity-p2p/releases`, and atomically switches `current`.
The restricted deployment account can do this using its existing `www-data`
group membership. No Node daemon, proxy module, administrator account or new
server is needed.

`config.php` is created once outside the webroot and preserved on subsequent
deployments. Only the PHP worker creates `run/state`, mode 0700. Runtime state
and configuration survive website deployments. STUN is enabled; TURN and
gameplay forwarding are not supported. The PHP endpoints serve admission,
public lobby/chat, SDP/ICE and match roster closure only.

The local analytics hook records schema-3 `direct-p2p` participation in the
existing SQLite lifecycle table. Runtime is a client claim; transport source is
the signaling service, not an assertion that PHP observed gameplay packets.
Schema-1/2 relay records and legacy match tables keep their original meanings.
Migration is transactional. Take a SQLite backup before the first deployment.

For a source rollback, redeploy the previous verified service manifest. Preserve
the additive analytics schema and receiver: older relay clients remain compatible
with them. Do not delete runtime state or restore an old database over new records.

Checks: `python3 deploy/test-p2p-install.py`, the analytics Python/PHP tests,
and `python3 deploy/check-web-security.py`. After deployment, check
`https://dunelegacy.com/p2p/v1/health`, origin rejection, a fresh public room and
browser/native participation rows. A successful health check does not establish
gameplay or Internet NAT reachability; verify those in actual matches.

## Committed release preview

The optional `deploy/release-preview.json` enables one isolated, source-pinned preview
through the existing **Deploy to Droplet** job. It does not replace `website/play`,
the production `p2p-service`, or the production `/p2p` entrypoint. There is no preview
daemon and no new key. Do not upload an untracked preview into the webroot: the next
ordinary/hourly `rsync --delete` removes it.

After the integrator freezes the candidate, commit all three together:

- Browser package at `website/play-test-865-249aa55e5/`, produced from the exact
  candidate with the normal game `scripts/package-web.py` and production signaling
  origin. Keep split data parts in Git; deployment joins them before serving.
- Matching generated `scripts/package-p2p-service.py` output at `p2p-preview/`.
  Run the generator under the workspace coordinator; never handcraft `SOURCE.json`.
- `deploy/release-preview.json` with `path: "play-test-865-249aa55e5"`,
  `sourceCommit: "249aa55e5e2d0935c25ec4279fd4e3d7621fb949"`, `version: "1.0.865"`
  and `gameProtocol: 122`. Copy `deploy/p2p-preview-entry.php` unchanged into the
  browser package's `p2p/index.php`; copy matching service routes into `p2p/.htaccess`.

These are the 1.0.865 candidate identities, not permission to publish another pin.
Future candidates must use their own immutable source-prefixed path. The installer
rejects browser/service/version/routing/entrypoint drift, validates the browser CSP
and all artifact hashes, and uses the existing service manifest verifier/PHP lint.

The private preview root is `/var/www/data/dunecity-p2p-previews/<preview-name>`.
Its preserved `config.php` points only to its own `run/state`, exact public TLS base
path, candidate protocol, the production Google STUN pair and exact
`https://dunelegacy.com` origin. Trusted schema-3 lifecycle events go to its own
`run/games.sqlite`; they are not HMAC relay delivery or observed gameplay packets.
Preview lobbies do not post Discord notifications or named production activity.
Ordinary deployments reinstall the same immutable snapshot and preserve all of this
state; committed browser assets survive webroot synchronization.

Before merge/deploy, report the exact diff, prior source and rollback to the release
integrator and obtain the relevant GO. Run `python3 deploy/test-p2p-install.py`,
`python3 deploy/test-release-preview.py` and the existing web-security workflow once
after source editing finishes. With the package committed, also run
`python3 deploy/install-release-preview.py --repository . --website website --validate-only`.
Require successful deployment, live preview `build.json` source/hash checks and
`/<preview-name>/p2p/v1/health` before actual public browser/native gameplay.

**Inspect release preview** is an operator-only workflow using the existing pinned
deployment SSH secret, without root/bootstrap fallback. It verifies installed
preview source hashes and reads one own room selected by the exact observed
wire `appVersion` and a narrow UTC window. Zero/multiple matching rooms are refused.
The window uses Unix seconds, at most one hour within the past day. JSON artifacts omit
tokens, invitation codes, SDP, ICE and addresses. It does not inspect or operate the
legacy relay, read production analytics, invoke a launcher or stop a process.

For an approved outage check, select `outage_seconds: 60` with an existing own preview
room. This atomically sets a private `outage-until` timestamp. Only the preview
wrapper returns 503, and the flag expires automatically even if the runner dies.
There is no public mutation route, no production outage flag and no daemon stop.
Verify 503 during the window, continued direct gameplay, then health recovery;
do not infer those results from workflow submission.

Rollback/retirement: after the evidence owner confirms matches are closed and logs
are retained, remove the preview descriptor, its committed browser directory and
`p2p-preview` in a reviewed PR. The ordinary deploy then removes only that public
preview. Keep its private config/state/SQLite and operator evidence; never delete
or restore production data. If only reverting the mechanism, remove the descriptor
first so old deployments cannot strand a public entrypoint. Production source
rollback remains the five-file manifest/source rollback described above.


## Discord announcements

The entrypoint connects the trusted signaling notification hook to
`metaserver/p2p_notifications.php`. It reuses `/var/www/data/discord_webhook.txt`
(or `DISCORD_WEBHOOK_URL`) and announces custom/campaign lobby creation, starts
and hot joins for both public and private rooms. Starts name the host and list the
human players present. Hot joins name the participant and distinguish spectator
admission from an approved player joining when the host resumes. Human player and
spectator rosters are separate; AI names are not available to signaling. Requests,
approvals and aborted controller transfers do not announce a player join. Invitation
codes and transport credentials are never included. Player names render as literal
text, with mentions disabled. No map/mod metadata is claimed in these messages.

The private `discord-p2p` outbox deduplicates by room log ID and event kind (plus
participant ID and role for hot joins, allowing spectator-to-player promotion), keeps
at most 256 jobs for an hour, and drains one due job on subsequent P2P requests.
Delivery is globally paced and network calls hold no queue/room locks. HTTP 429
and transient failures retry up to five attempts; retry delays are respected up
to fifteen minutes. Permanent authorization/not-found failures are logged and
stopped. `wait=true` requires a confirmed Discord message ID; diagnostics contain
only event IDs, HTTP status and confirmed message IDs. A timeout after Discord has
accepted a message can still cause a duplicate on retry. Retries require traffic;
this does not install a daemon. FPM finishes the game response before delivery;
on Apache each delivery adds at most two seconds to that request.

Check `php scripts/tests/test_p2p_notifications.php` and the signaling integration
suite before publishing. Verify the configured webhook with a read-only GET. A
live test post needs explicit authorization; local tests use a fake sender.

Verified 14 September 2026 (Sydney): game service PR37/source709d0a2 and website
e4edc3b deployed successfully in run34765919342. Read-only webhook inspection
confirmed the existing Dune Multiplayer Server channel. Stefan authorized two
live test messages. An actual private custom admission/host session/start emitted
Discord messages1548718423914057941 (Lobby Created) and1548718476431069229
(Starting); both were read back through Discord and contain no invitation/session
credentials or mentions. Repeating the start emitted no duplicate. The temporary
test lobby was removed. Local tests passed166 signaling cases plus notifier
payload/deduplication/privacy/rate-limit/transient/permanent-error cases and the
service installer/security checks. Evidence is in the local project outputs:
`outputs/dunecity-menu-acceptance/discord-live-verification.json`.

## Public activity and waiting presence (client 1.0.725)

`metaserver/public_activity.php` connects the trusted local
`dunecityP2PRecordPublicActivity` hook to the additive `analytics_public_activity`
table in `games.sqlite`. PHP/PDO and the existing Python fallback both support it.
The table is created lazily on the first event; no existing match/lifecycle table
is replaced. Apply `metaserver/public_activity.sql` ahead of time if an empty table
is needed for administration. Back up SQLite before initial installation.

Records contain accepted public chat text and its session display name, the host
of a newly seated public game, newly seated public players, and an authoritative
admitted roster when a public match starts. Roster details include peer IDs, names,
roles and claimed runtime. Creation/join/start retries deduplicate by event ID;
conflicting reuse is refused. Only public rooms enter this named history. The
separate anonymous lifecycle and Discord behavior stay unchanged. No invitation
codes, tokens, SDP, ICE or IP addresses enter the new table. Names are display
identities, not verified accounts. There is no public ingestion/read endpoint.

Server `analytics_enabled` controls capture. Client development diagnostics do not.
The table is durable history, separate from the short chat display buffer; there
is no automatic expiry or historical backfill. Hook/storage failures allow the
user's accepted action to complete and report a generic server error; there is
no named-event retry journal, so outages can leave gaps.

The same packaged service supports `allMods=1` directory queries (legacy requests
remain compatible) and `presence=1` chat polls. Presence counts sessions active
within twenty seconds, across mods sharing a game protocol; at most twelve names
are returned alongside the total. It excludes players whose waiting-screen poll
has stopped. Presence is transient and is not written to analytics.

Validation: `python3 scripts/tests/test_public_activity.py` checks PHP/Python
validation, both storage paths, UTF-8 text, duplicate/conflicting events, complete
start rosters and preservation of existing records. Run the game service's
`test/test_public_activity.py` for authoritative event creation and presence.

Example read-only queries against the private metaserver database:

```sql
SELECT datetime(occurred_at,'unixepoch') AS time, kind, player_name, message
FROM analytics_public_activity ORDER BY occurred_at DESC LIMIT 50;
SELECT a.room_id, a.occurred_at, json_extract(p.value,'$.name') AS player,
       json_extract(p.value,'$.role') AS role, json_extract(p.value,'$.runtime') AS runtime
FROM analytics_public_activity AS a, json_each(a.details_json,'$.players') AS p
WHERE a.kind='public_game_started' ORDER BY a.occurred_at DESC;
```

## Hot joining and passive spectators (client 1.0.729)

Protocol 8 admits spectators without changing the running controller roster,
match phase or epoch. The service binds the role to the approved name/claims,
restricts each observer's signaling to the host and excludes viewers from start
roster checks. The host sends an independent bounded checkpoint/tick stream;
only the viewer loads and catches up. Viewer congestion, missing acknowledgements
and departures do not delay active players. Rejected play requests become
spectators. Exact client versions must match. Co-op retains two controllers and
the service retains its eight-connection bound including observers.

Older protocols retain their existing admission behavior. Gameplay state remains
peer-to-peer; the metaserver does not relay snapshots or game commands. Spectator
admission retains named public seating events; committed start rosters contain
controllers. The current activity schema does not store a separate spectator flag.
The game repository's docs/late-join-protocol.md specifies packet limits and
observer-only runtime continuation. Verified with 188 real-HTTP tests, busy
three-peer stalls/timeouts and an actual browser over isolated public HTTPS.

Keep `website/p2p/.htaccess` identical to `p2p-service/public/.htaccess`: the
request, request-status and join-requests routes must reach PHP. The website
security check now rejects routing drift before deployment. Existing private
configuration, runtime state and named activity history are preserved.

## Workshop content service (client 1.0.740 candidate)

The service snapshot adds immutable mod/map revision storage and six content API
routes, plus admission/inspect for required content discovery before joining.
Existing admission, room state and signaling contracts are preserved; no game
protocol is forced in the preserved operator configuration. The new app still
requires matching client versions when joining, as older releases did.

Keep the public and private .htaccess files identical. Both route lists include
the content endpoints and inspect; the outer request bound is 524288 bytes for
hex-encoded manifests. PHP retains smaller limits on signaling requests.
Content is stored under the existing private state directory, with worker-owned
0700 directories, immutable SHA256 blobs, upload ownership capabilities and a
bounded quota (20 GiB by default, configurable with content_quota_bytes).
Existing runtime state, analytics and configuration are preserved by deployment.

Before deployment, 207 real-HTTP/PHP and ingress-contract tests passed in the game
checkout. Forty admission/grant/signaling/start tests passed with 1.0.737/protocol9
claims and another forty with protocol8. These verify protocol compatibility,
not old executable playback. The local 740 browser reaches Campaign gameplay
automatically and retains the explicit Play Online pregame lobby.

Rollback: restore the preceding service snapshot and matching public route file,
leaving configuration, rooms and content state intact. Older clients can continue
using their original routes; 740 sharing will be unavailable after rollback.

## Map catalogue (client 1.0.765)

The additive `catalogue=maps` content listing serves latest map revisions with mod,
width, height and maximum-player metadata. Existing content clients are unchanged.
The service state retains `content/maps/<revision>.ini` and `.json` metadata,
using hard links to verified blobs so map copies do not bypass storage quotas.
Normal deployment preserves this state. Game source commit 33e75fd4255a20d7276596526010da172cac7b71 contains
the catalogue integration tests and seeding CLI. Names stay stable; versions are
metadata. Rollback restores the previous service snapshot; uploaded revisions remain
valid and accessible through the original content API.

Map category metadata now uses schema 3: placed buildings determine Vanilla, Tornie
or DuneCity. Old tags, names and dependency bases do not determine the category.
Exact mod dependency snapshots remain separate and unchanged. Existing metadata
refreshes lazily; catalogue deduplication preserves distinct pinned dependencies.

Schema 4 also preserves city-named starter maps as DuneCity when their functional
building count is at most max(4, twice the player count); walls/slabs are ignored.
