# Browser matchmaking service

The browser Find Match flow uses the upstream P2PKit global FIFO matcher at
`257f3c8eb0c0cf373298225e54c8d09d2f896a42`. The exact upstream server is retained
in `matchmaking-service/server.js` with a provenance header. Only a health
endpoint and graceful service lifecycle are added in `service.mjs`. The `ws`
dependency is pinned with npm integrity metadata; install with `npm ci --ignore-scripts`.

This is separate from `/p2p` room admission and the legacy polling relay. It
carries introductions and ICE/SDP, not game commands. No TURN server is added.

## Production installation

Administrator access is required for the initial TLS proxy and systemd unit.
`install-matchmaking.sh BUNDLE SHA256 FULL_COMMIT` performs the checked installation
on the existing Linux x64 host. The bundle is a git archive containing
`matchmaking-service` and the deployment files, supplied with its independent hash.
Deploy reviewed, committed files and locked dependencies into a root-owned
`/opt/dunecity-matchmaking/releases/<commit>` directory, then atomically switch
`current`. The pinned Node 22.23.2 x64 archive SHA-256 is
`d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307`.
Install that verified runtime under `/opt/dunecity-matchmaking/node`.

Install `dunecity-matchmaking.service` under `/etc/systemd/system` and enable it.
It runs as a dynamic unprivileged user with a read-only filesystem, a private
temporary directory, memory/task limits, automatic restart, and loopback binding.
The daemon has no deployment keys, application secrets, or database access.

Enable Apache `proxy`, `proxy_http`, `proxy_wstunnel`, and `rewrite`. Install
`dunecity-matchmaking-apache.conf` under `/etc/apache2/snippets` and include it
inside the existing dunelegacy.com TLS virtual host. Back up that virtual host
before editing it. Run `apache2ctl configtest` before a graceful Apache reload.
The `/` route is proxied only for WebSocket Upgrade requests, matching the SDK's
default. Normal website requests keep their existing behavior. `/matchmaking`
is also supported for explicitly configured clients. Preserve Host/Origin.

Health: `curl --fail http://127.0.0.1:8788/health`. Verify root and dedicated-path
WSS handshakes, pairing, forwarding, and departure cleanup before publishing a
client. Restart the service once and verify recovery before first use. Stopping
it loses its in-memory queue; it does not affect the separate existing room service.

The installation stays outside the rsync-managed webroot, so routine website
updates cannot delete it. Future service updates use a reviewed release directory
and a controlled restart, not arbitrary root execution by the restricted deploy user.
Rollback: restore the previous `current` symlink and restart only this unit; restore
the backed-up TLS vhost and reload Apache if removing the new routes is necessary.

Tests: `npm ci --prefix matchmaking-service --ignore-scripts` followed by
`npm test --prefix matchmaking-service`. Public gameplay and Internet NAT
reachability are separate checks; successful health or loopback tests do not prove them.
