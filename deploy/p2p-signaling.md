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

## Discord announcements

The entrypoint connects the trusted signaling notification hook to
`metaserver/p2p_notifications.php`. It reuses `/var/www/data/discord_webhook.txt`
(or `DISCORD_WEBHOOK_URL`) and announces custom/campaign lobby creation and starts
for both public and private rooms. Invitation codes and transport credentials are
never included. Current signaling does not receive map/mod metadata, so messages
use the known host, mode, version, visibility and player counts only.

The private `discord-p2p` outbox deduplicates by room log ID and event kind, keeps
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
