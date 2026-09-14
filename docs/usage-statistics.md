# Public game usage statistics

`website/usage.html` fetches `metaserver/usage.php`, which invokes the fixed
`usage_stats.py` helper against `/var/www/data/games.sqlite` in read-only mode.
Only aggregate JSON is published: no player names, match IDs or raw events.
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

## Operations

A good cache remains available during refresh or helper failure; the UI flags
stale data after 15 minutes. Without a good cache the endpoint returns 503.
Inspect permissions, Python errors and database availability if refresh fails;
do not alter or delete the analytics database to fix the public page.
Tests: `python3 -m unittest discover -s scripts/tests -p 'test_usage_stats.py'`.
PHP syntax and aggregation tests run in compatibility and deployment workflows.
