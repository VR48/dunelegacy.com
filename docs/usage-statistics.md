# Public game usage statistics

`website/usage.html` fetches `metaserver/usage.php`, which invokes the fixed
`usage_stats.py` helper against `/var/www/data/games.sqlite` in read-only mode.
Maps list only the 15 most-played names per period (`MAP_LIMIT`), which is all
the page shows; publishing every map name made the report about 0.5 MB.
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
- Session counts are sessions, not unique people. Retries and manual development
  games count. Player counts are described under "Players" below. Probe and local multiplayer test games (isolated `DUNECITY_USERDIR`
  profiles) stop reporting from game commit 2dc819da; the 886 probe games
  recorded before were deleted on 2026-10-31 (backup
  `/var/www/data/backups/games-pre-probe-cleanup-20261031T113922Z.sqlite`).
- Mods: a pinned copy `ws-<sha256>` (an older version kept for a save, campaign
  or online game) counts as the shipped mod its manifest in the community content
  store names as base: Vanilla, Dune City, Dune2R or one of the Tornie-family
  mods (Tornie, Tornie Lite, Jericho, Jericho Lite, ArcaKev, ArcaKev Lite), without a version.
  Copies of any other mod, or missing from the store, count as Vanilla.
- Platforms are the client-reported runtime: browser, desktop (`native`),
  Android and iPhone/iPad (`android`/`ios`, from 1.0.857; older Android builds
  report `native`). Unknown runtime stays separate; never infer a platform from
  missing fields. Platform percentages use identified sessions only.
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

## Players

Each period, day and month also reports distinct players (`players`,
`daily_players`, `monthly_players`): per platform, the total, how many of them
were counted by installation key (`install_ids`), the sessions in that bucket
and the sessions without an identified player (`unidentified_sessions`).

- Only player rows whose recorded controller is `human` count.
- A player is identified by `analytics_players.install_key`, the keyed hash of
  the random installation id a game sends (see `metaserver/ANALYTICS.md`).
  Without one, the display name stands in: case-folded with whitespace
  collapsed. A name the data links to exactly one installation key counts as
  that installation, so games from before and after a client sent its id are
  one player. A name linked to several keys stays a separate name identity.
- Names many people share identify nobody and are not counted (`SHARED_NAMES`
  in `usage_stats.py`): the game defaults `Player` (browser) and `Mobile`
  (iPhone/iPad), and computer account names such as `User`, `Admin` or a
  laptop brand. A session whose human players all have such names, or that has
  no human player row, counts as a session without an identified player.
- A player on two platforms counts in both platform columns and once in the
  total. Someone using two names or devices counts more than once.
- Identities are never published: the output has counts only.
- History before installation ids is counted by name only. Desktop and Android
  names are mostly distinct (computer or app account names), so most of those
  sessions are identified. Browser and iPhone/iPad games almost all use the
  default name, so those players are mostly missing from historical counts.

## What players play

Each period has `players_by` and `player_mix`, built from the same player
identities as above (counts only, never identities):

- `players_by.modes`, `players_by.mods`, `players_by.levels`: distinct players
  per mode, mod and Dune Legacy campaign level, per platform, with sessions and
  sessions without an identified player. A player counts in every row they
  played, so rows add up to more than the period's players.
- `player_mix.modes`, `player_mix.mods`: each identified player counts once,
  under the exact set of modes (or mods) they played in the period, for example
  Campaign + Skirmish. The `MIX_LIMIT` (12) most common combinations are listed;
  `other_players` and `other_combinations` sum the rest.

## What players like

Each period has `engagement` with five tables: `modes`, `mods`, `maps` (the
listed top maps), `levels` (Dune Legacy campaign) and `dunecity_missions`. Each
has `rows` and a pooled `baseline` of the table's rates.

Per row: `games`; `ended` (games with an end report) and `quits` (outcome
abandoned); `measured` and `quick_quits` (abandoned within 60 s of an observed
start); identified `players`, `repeat_players` (3+ games) and
`returning_players` (played on 2+ UTC days); `median_finished_seconds`. Levels
and missions instead report `continued_players`: players who also started the
next level or mission in the period (null for the last one present), because
replaying a mission is usually retrying, not liking it. Repeat and returning
play are not verdict signals there.

`verdict` compares the row with the table's baseline, so it ranks rows against
each other rather than judging them in absolute terms:

- good (`good`): returning or repeat rate at least 1.2x baseline, continued
  rate at least 1.2x, quit rate at most 0.8x;
- bad (`bad`): quick-quit rate at least 1.5x, quit rate at least 1.25x,
  returning or continued rate at most 0.6x;
- `liked`, `disliked`, `mixed` (both) or `average` (neither); `too_few` below
  20 games or 5 identified players.

A game without an end report is neither a quit nor finished. In the 24-hour
view almost nobody can return on another day, so a zero baseline gives no
returning signal.

## DuneCity Campaign

Each period has `dunecity_campaign`: games, players (as above), human wins and
losses, one row per mission and daily games and players. A game belongs to the
campaign when its map file name is `DCITY_M01`..`DCITY_M10` (optionally `.INI`,
any directory), whatever the reported game type; this mirrors the game's
`GameInitSettings::isDuneCityCampaign()`. The game already reports this name, so
no client change was needed.
The game reports these missions with game type `campaign`; everywhere else in
the report (modes, players by mode, AI players, game lengths) they are the
separate mode `dunecity_campaign` (AI label "DuneCity Campaign"), so Dune
Legacy campaign figures and levels never include them, and they are not listed
among custom and multiplayer maps. Mission names come from the mission files' header
comments in the game repository (`DUNECITY_MISSIONS`). Per mission: starts
(every game, retries included), distinct players, games with a human win, a
human loss, an early exit and no end report. Daily rows start at the first
campaign game within the period.

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

A request never waits for the helper while a report exists. A report younger
than five minutes is served as is. An older one is served at once, the response
is completed (`Content-Length`, `Connection: close`, flush), and the same request
then rebuilds the cache under the lock; the page fetches again 15 seconds after
receiving a report older than five minutes (the server cache lifetime). Only the very first request, with no
report at all, waits. Each refresh also writes `usage-public-v1.json.gz`, served
with `Content-Encoding: gzip` to clients that accept it when it is at least as
new as the JSON (about 10 KB instead of about 65 KB). A good cache remains
available during refresh or helper failure; the UI flags stale data after 15
minutes. Without a good cache the endpoint returns 503.
Inspect permissions, Python errors and database availability if refresh fails;
do not alter or delete the analytics database to fix the public page.
Tests: `python3 -m unittest discover -s scripts/tests -p 'test_usage_stats.py'`;
`test_usage_endpoint.py` runs `usage.php` under `php -S` with a temporary
`DATA_DIR` (first build, gzip copy, stale answer then rebuild).
PHP syntax and aggregation tests run in compatibility and deployment workflows.
