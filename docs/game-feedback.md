# In-game feedback service

`POST https://dunelegacy.com/metaserver/feedback.php` creates public issues in
`ggtothemax/dunecity`. Players need no GitHub account. The game sends only the summary,
feedback text, and the game details shown in its dialog (version, platform, mod,
house, mission, and AI types/difficulties). The server attaches an opaque request
id for retry reconciliation. It never attaches IP addresses or human player names.

## Provision and deploy

Storage uses PDO SQLite when available, otherwise the existing Python 3 SQLite
runtime through a bounded private worker. This needs no administrator install.
The worker holds one connection per request so reservation transactions remain
atomic. Its script is blocked by Apache and never receives credentials or drafts.
Deployment checks the Apache storage path using an invalid submission that creates
no issue. Tests run the same behavior suite against both storage backends.

Create a fine-grained GitHub personal access token restricted to **ggtothemax/dunecity**,
with **Issues: read and write** and GitHub's required Metadata read permission.
Do not grant Contents access or distribute this token with desktop/browser builds.
GitHub documents the permission for [Create an issue](https://docs.github.com/en/rest/issues/issues#create-an-issue).

Store the credential as the website repository's Actions secret
`FEEDBACK_GITHUB_TOKEN`. The existing deployment workflow writes it over SSH to
`/var/www/data/feedback_github_token.txt` with mode 0640, readable by the web server.
The data directory has the www-data setgid bit. It stays outside the published web
root and survives code deployments. A missing Actions secret preserves an existing
server token; an unconfigured server returns an explicit error instead of success.
The production token uses no expiration, as requested by the maintainer.
To rotate it, replace the secret and redeploy. To revoke access, revoke the
GitHub token and remove the server token file.

The website repository contains the authoritative endpoint source. Deploy through
its normal main-branch workflow after approval. `feedback.sqlite` also lives in the
persistent data directory; back it up with the other metaserver data. It contains
request hashes, ids and confirmed URLs, not feedback bodies. Application logs must
not capture POST bodies or authorization headers.

## Contract and failure handling

Form fields: `request_id` (32 lowercase hexadecimal characters), `title` (100 Unicode
characters), `details` (2,000 Unicode characters), `context` (8,000 bytes).
Success is exactly `OK https://github.com/ggtothemax/dunecity/issues/<number>`.
Application errors start with `ERROR `; clients retain drafts and offer retry.
No browser opens until the player chooses **View request** after confirmed success.

HTTPS is required. The endpoint accepts same-origin browser POSTs and native
clients, caps requests at 64 KiB, rejects malformed UTF-8, and limits upstream calls
to six per hour per hashed source address and sixty per hour globally. Cached
success retries do not consume upstream calls. Repository, API URL, labels and
token are controlled by the service, never request parameters.

An SQLite reservation prevents simultaneous retries from creating duplicate
issues. Explicit upstream rejection can be retried. If the creation response was
lost or ambiguous, retries only reconcile against the newest 100 repository issues
using the request marker; they never repeat the creation POST. An older ambiguous
request outside that window needs operator reconciliation in the database. Do not
silently delete pending reservations and repost them.

## Verification

`php scripts/tests/test_feedback.php` tests creation, context, Unicode boundaries,
validation, idempotency, lost-response reconciliation, rejection retry and rate
limits with an injected fake GitHub transport. It creates no public test issues.
`php -l metaserver/feedback.php` and `php -l metaserver/feedback_service.php` lint the
entry point and service. The deployment workflow runs these checks before deploy.
