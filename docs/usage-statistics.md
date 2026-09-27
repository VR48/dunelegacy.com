# Public game usage statistics

`website/usage.html` fetches `metaserver/usage.php`, which invokes the fixed
`usage_stats.py` helper against `/var/www/data/games.sqlite` in read-only mode.
The default response publishes aggregate JSON. The multiplayer history response
also publishes recorded display names and game metadata, without match IDs,
player IDs or raw events.
The cache and lock are outside DocumentRoot under `/var/www/data/usage-public*`.
Apache denies direct access to the Python helper. The existing website deployment
publishes all these files together; no database migration is needed.

## Reporting rules

- Last 24 hours, last 30 days and all recorded time use session start times.
- Daily/monthly buckets use UTC. Partial periods show only observed data, and
  zero days are filled only after tracking began. Refresh interval: five minutes.
- Counts are sessions, not unique people. Retries and development games count.
- Unknown runtime stays separate from browser/native; never infer desktop from
  missing fields. Browser/desktop percentages use identified sessions only.
- Network multiplayer is the recorded game mode; single-player does not imply
  the computer was disconnected from the internet.
- Campaign scenarios map to levels: 1 => 1; 2–4 => 2; 5–7 => 3; 8–10 => 4;
  11–13 => 5; 14–16 => 6; 17–19 => 7; 20–21 => 8; 22 => 9.
  This follows the game missionNumberToLevelNumber implementation, not an
  assumption that every level contains three scenarios.
- A missing end report is not a loss or confirmed quit. Human win/loss counts
  require a finished result. Multiplayer can have both a human win and loss.
- Lengths are observed server start/end wall intervals (including pauses), not
  simulation time. Exclude end-only reports with inferred starts. Campaign
  level 1 is a subset, not an additional mode. Quick exits are confirmed early
  exits after at most 60 seconds.
- AI counts deduplicate the same bot type per match and relationship to a human:
  same house, allied house or opponent. Different types/difficulties may overlap.

## Multiplayer history

`usage.php?history_page=1` returns the newest 20 qualifying games. Positive integer
page numbers select older pages with SQL LIMIT/OFFSET; the full recorded history
is accessible independently of the aggregate date selector. The response includes
page, page_size, pages, total and games. Pages beyond the end contain no games.
Each game lists its UTC start time, map, player display names and controller types,
mod and game version. Missing metadata is shown as unknown or a dash.

A game must have `game_type = multiplayer` and at least two stored player rows
whose controller is `human`. AI, spectator and unknown controllers never count.
Human players sharing a house still count separately. Older legacy announcements
without controller data cannot qualify. Current clients report from the host only;
the match primary key handles repeat reports, and distinct games are never merged
by similar names or start times. Ordering uses start time then match ID for ties.

The helper's `--history-page N` mode queries one page in a read-only transaction.
The default helper output and aggregate endpoint remain unchanged. History requests
use one process lock and return 503 when busy or unavailable. No per-page cache files
are created. `DATA_DIR` can select a fixture directory for local endpoint tests;
production defaults to `/var/www/data`. The history section loads independently of
aggregate statistics and refreshes every five minutes.

## Operations

A good cache remains available during refresh or helper failure; the UI flags
stale data after 15 minutes. Without a good cache the endpoint returns 503.
Inspect permissions, Python errors and database availability if refresh fails;
do not alter or delete the analytics database to fix the public page.
Tests: `python3 -m unittest discover -s scripts/tests -p 'test_usage_stats.py'`.
PHP syntax and aggregation tests run in compatibility and deployment workflows.
