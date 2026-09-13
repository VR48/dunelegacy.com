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
