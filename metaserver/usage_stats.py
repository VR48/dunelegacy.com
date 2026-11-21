#!/usr/bin/env python3
"""Public aggregates and one page of multiplayer history.

Read the live SQLite database without changing it.  Without arguments the output is the
aggregate report exactly as before; --history-page N returns that page of the multiplayer
history and nothing else.
"""
import collections
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time

UTC = dt.timezone.utc
RUNTIMES = ('browser', 'native', 'android', 'ios', 'unknown')   # native is desktop
AI_NAMES = {'qbot': 'QuantBot', 'campaign': 'Campaign AI', 'classic': 'Classic AI',
            'smartbot': 'SmartBot', 'mentat': 'Mentat'}
HISTORY_PAGE_SIZE = 20
MAP_LIMIT = 15   # the page lists the most-played maps; publishing every map name made the report ~0.5 MB
# The DuneCity Campaign (1.0.875): ten missions DCITY_M01..M10, whatever the reported game type,
# matching the game's GameInitSettings::isDuneCityCampaign() file name test.
DUNECITY_MISSION = re.compile(r'DCITY_M(\d{2})(?:\.INI)?', re.I)
DUNECITY_MISSIONS = ('Sihaya Basin', 'Hagal Flats', 'Ash Quarter', 'Arrakeen Blackout', 'Cielago Watch',
                     'Harg Pass Convoy', 'Shield Wall Breach', 'Tuono Crossing', 'Carthag Vise',
                     'All Against Atreides')
HISTORY_MAX_PAGE = 10 ** 7
# Modes as players see them. A DuneCity Campaign mission reports game type "campaign" but is its own
# mode here, so the Dune Legacy campaign figures never include it.
MODE_ORDER = ('campaign', 'dunecity_campaign', 'skirmish', 'single_custom', 'multiplayer', 'load', 'unknown')
MIX_LIMIT = 12   # most common combinations of modes or mods per player; the rest are summed
# Engagement: what players like (they come back and play again) and dislike (they quit, often at once).
QUICK_QUIT_SECONDS = 60
REPEAT_GAMES = 3            # a player who played something this many times kept playing it
VERDICT_MIN_GAMES = 20      # rows with fewer games or identified players get no verdict
VERDICT_MIN_PLAYERS = 5
# A game qualifies only on recorded controllers: two or more player rows that the reporting
# client called human.  Unknown or missing controllers never qualify a game.
HISTORY_WHERE = '''FROM analytics_matches AS m WHERE m.game_type = 'multiplayer'
    AND m.started_at IS NOT NULL AND m.started_at <= ?
    AND (SELECT COUNT(*) FROM analytics_players AS p
         WHERE p.match_id = m.match_id AND p.controller = 'human') >= 2'''
HISTORY_MATCH_COLUMNS = {'match_id', 'started_at', 'game_type', 'map_name', 'mod_name', 'game_version'}
HISTORY_PLAYER_COLUMNS = {'match_id', 'slot', 'player_name', 'house_name', 'controller', 'ai_type'}
# The game names an exact pinned mod copy `ws-<sha256>`. Its manifest in the community content
# store records the mod it was based on. Copies of a shipped mod count as that mod; copies of
# anything else, or missing from the store, count as vanilla. Mod names come from clients, so
# only a well-formed digest is ever used as a file name.
CONTENT_DIR = '/var/www/data/dunecity-p2p/run/state/content'
SHIPPED_MODS = ('vanilla', 'dunecity', 'Tornie', 'Dune2R', 'TornieLite', 'Jericho', 'JerichoLite',
                'ArcaKev', 'ArcaKevLite')
SNAPSHOT = re.compile(r'ws-([0-9a-f]{64})')
# Players are counted by identity, never published: the keyed hash of a reporting installation's
# random id (install_key) when the game sent one, otherwise the recorded display name. Display names
# that many different people share identify nobody: the game's own defaults ("Player", and
# "Mobile" on iPhone and iPad) and common computer account names. Sessions whose human players
# have only such names count as sessions without an identified player.
SHARED_NAMES = frozenset((
    'player', 'mobile', 'user', 'admin', 'administrator', 'owner', 'root', 'guest', 'test', 'me',
    'home', 'pc', 'windows', 'asus', 'lenovo', 'hp', 'dell', 'acer', 'deck', 'steamuser', 'steam',
    'gebruiker', 'usuario', 'utilisateur', 'benutzer', 'utente', 'web_user', 'ubuntu', 'pi',
    'default', 'unknown'))


def snapshot_base(digest, content_dir):
    try:
        with open(os.path.join(content_dir, 'manifests', digest), 'rb') as manifest:
            lines = manifest.read(1 << 20).split(b'\n')
    except OSError:
        return 'vanilla'
    base = next((line[5:] for line in lines if line.startswith(b'base=')), b'')
    try:
        base = bytes.fromhex(base.decode('ascii')).decode('utf-8')
    except (UnicodeDecodeError, ValueError):
        return 'vanilla'
    return base if base in SHIPPED_MODS else 'vanilla'


def mod_labeller(content_dir):
    """Public mod label for a recorded mod name; looks each snapshot up once."""
    bases = {}
    def label(name):
        match = SNAPSHOT.fullmatch(name or '')
        if not match:
            return (name or 'unknown')[:80]
        if match[1] not in bases:
            bases[match[1]] = snapshot_base(match[1], content_dir)
        return bases[match[1]]
    return label


def iso(timestamp):
    return dt.datetime.fromtimestamp(timestamp, UTC).isoformat()


def level(name):
    match = re.fullmatch(r'SCEN[A-Z](\d{3})', name or '', re.I)
    n = int(match[1]) if match else 0
    return 9 if n == 22 else (n + 1) // 3 + 1 if 1 <= n <= 21 else None


def runtime(value):
    return value if value in RUNTIMES else 'unknown'


def matrix(rows, key):
    counts = collections.defaultdict(lambda: dict.fromkeys(RUNTIMES, 0))
    for row in rows:
        label = key(row)
        if label is not None:
            counts[str(label)][runtime(row['client_runtime'])] += 1
    return [dict(label=k, **v, total=sum(v.values()))
            for k, v in sorted(counts.items(), key=lambda item: (-sum(item[1].values()), item[0]))]


def outcomes(rows, players):
    finished = [r for r in rows if r['outcome'] == 'finished']
    wins = losses = unclassified = 0
    for row in finished:
        humans = [p for p in players[row['match_id']] if p['controller'] == 'human']
        # Count a match once; multiplayer may contain both winners and losers.
        if any(p['result'] == 'winner' for p in humans):
            wins += 1
        if any(p['result'] == 'defeated' for p in humans):
            losses += 1
        if not any(p['result'] in ('winner', 'defeated') for p in humans):
            unclassified += 1
    return {'sessions': len(rows), 'finished': len(finished), 'human_wins': wins,
            'human_losses': losses, 'unclassified_finished': unclassified,
            'exited': sum(r['outcome'] == 'abandoned' for r in rows),
            'missing_end': sum(r['ended_at'] is None for r in rows)}


def lengths(rows, players):
    # An end-only upsert has an inferred start; it is not an observed wall interval.
    ended = [r for r in rows if r['has_start'] and r['ended_at'] is not None
             and r['started_at'] is not None and r['ended_at'] >= r['started_at']]
    durations = sorted(r['ended_at'] - r['started_at'] for r in ended)
    longest = max(ended, key=lambda r: r['ended_at'] - r['started_at']) if ended else None
    result = outcomes(rows, players)
    result.update({'measured': len(ended),
                   'average_seconds': sum(durations) / len(durations) if durations else None,
                   'median_seconds': (durations[(len(durations)-1)//2] + durations[len(durations)//2])/2 if durations else None,
                   'longest_seconds': durations[-1] if durations else None,
                   'longest_outcome': None,
                   'quick_exits': sum(r['outcome'] == 'abandoned' and r['ended_at']-r['started_at'] <= 60 for r in ended)})
    if longest:
        humans = [p for p in players[longest['match_id']] if p['controller'] == 'human']
        result['longest_outcome'] = ('Early exit' if longest['outcome'] == 'abandoned' else
            'Human win' if longest['outcome'] == 'finished' and any(p['result'] == 'winner' for p in humans) else
            'Human loss' if longest['outcome'] == 'finished' and any(p['result'] == 'defeated' for p in humans) else 'Finished / unclassified')
    return result


def bot_stats(rows, players):
    counts = collections.defaultdict(set)
    for row in rows:
        mid = row['match_id']
        humans = [p for p in players[mid] if p['controller'] == 'human']
        for bot in players[mid]:
            if bot['controller'] not in ('ai', 'qbot'):
                continue
            relations = set()
            for human in humans:
                if bot['house_slot'] is not None and bot['house_slot'] == human['house_slot']:
                    relations.add('same_house')
                elif bot['team'] is not None and human['team'] is not None:
                    relations.add('ally' if bot['team'] == human['team'] else 'opponent')
            ai = AI_NAMES.get(bot['ai_type'], 'Other AI')
            mode = ('DuneCity Campaign' if row['mode'] == 'dunecity_campaign' else
                    'Campaign' if row['mode'] == 'campaign' else 'Other modes')
            rt = runtime(row['client_runtime'])
            for relation in relations:
                counts[('ai', mode + ' · ' + ai, relation, rt)].add(mid)
                counts[('assistance', relation, relation, rt)].add(mid)
                if relation == 'same_house':
                    counts[('assistance', 'Support AI' if bot['ai_support'] else 'Regular AI sharing control', relation, rt)].add(mid)
                if relation == 'opponent' and bot['ai_type'] == 'qbot':
                    difficulty = bot['ai_difficulty'] if bot['ai_difficulty'] in ('easy','medium','hard','brutal','defend') else 'unknown'
                    counts[('difficulty', difficulty, relation, rt)].add(mid)
    output = {}
    for category in ('ai', 'assistance', 'difficulty'):
        grouped = collections.defaultdict(lambda: dict.fromkeys(RUNTIMES, 0))
        for (cat, label, relation, rt), mids in counts.items():
            if cat == category:
                grouped[(label, relation)][rt] = len(mids)
        output[category] = [dict(label=label, relation=rel, **v) for (label, rel), v in
                            sorted(grouped.items(), key=lambda item: (-sum(item[1].values()), item[0]))]
    return output


def name_identity(value):
    name = ' '.join(display_name(value, '').casefold().split())
    return None if not name or name in SHARED_NAMES else 'name:' + name


def player_identities(players, columns):
    """Identities of the recorded human players in each match.

    An install key is authoritative. A distinctive display name stands in for it, and a name the
    data has linked to exactly one install key counts as that installation, so a player's games
    before and after their game reported an install id are one player.
    """
    keyed = 'install_key' in columns
    linked = collections.defaultdict(set)
    if keyed:
        for rows in players.values():
            for p in rows:
                name = name_identity(p['player_name']) if p['controller'] == 'human' and p['install_key'] else None
                if name:
                    linked[name].add('install:' + p['install_key'])
    aliases = {name: next(iter(keys)) for name, keys in linked.items() if len(keys) == 1}
    identities = {}
    for match_id, rows in players.items():
        found = set()
        for p in rows:
            if p['controller'] != 'human':
                continue
            if keyed and p['install_key']:
                found.add('install:' + p['install_key'])
                continue
            name = name_identity(p['player_name'])
            if name:
                found.add(aliases.get(name, name))
        identities[match_id] = frozenset(found)
    return identities


def player_counts(rows, identities, key):
    """Distinct identified players per label, overall and per platform, plus unidentified sessions."""
    groups = collections.defaultdict(lambda: collections.defaultdict(set))
    sessions = collections.Counter()
    unidentified = collections.Counter()
    for row in rows:
        label = key(row)
        found = identities.get(row['match_id'], frozenset())
        sessions[label] += 1
        if not found:
            unidentified[label] += 1
        groups[label][runtime(row['client_runtime'])].update(found)
        groups[label]['total'].update(found)
    return {label: dict(label=label, **{rt: len(g[rt]) for rt in RUNTIMES}, total=len(g['total']),
                        install_ids=sum(i.startswith('install:') for i in g['total']),
                        sessions=sessions[label], unidentified_sessions=unidentified[label])
            for label, g in groups.items()}


def players_by(rows, identities, key):
    """Distinct players per label (mode, mod, level), most players first."""
    counts = player_counts([r for r in rows if key(r) is not None], identities, lambda r: str(key(r)))
    return sorted(counts.values(), key=lambda r: (-r['total'], -r['sessions'], r['label']))


def player_mix(rows, identities, key, order=()):
    """How many identified players played each combination of labels (e.g. campaign + skirmish).

    Labels within a combination follow `order`, then alphabetical order. Only the MIX_LIMIT most
    common combinations are listed; `other_players` counts the players in the rest.
    """
    played = collections.defaultdict(set)
    for row in rows:
        for identity in identities.get(row['match_id'], ()):
            played[identity].add(key(row))
    rank = {label: i for i, label in enumerate(order)}
    combos = collections.Counter(tuple(sorted(labels, key=lambda l: (rank.get(l, len(rank)), l)))
                                 for labels in played.values())
    ranked = sorted(combos.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return {'players': len(played),
            'combinations': [{'labels': list(labels), 'players': n} for labels, n in ranked[:MIX_LIMIT]],
            'other_players': sum(n for _, n in ranked[MIX_LIMIT:]),
            'other_combinations': max(0, len(ranked) - MIX_LIMIT)}


def engagement_row(label, rows, identities):
    """Signals of liking and disliking for one group of games.

    Quits: an end report with outcome abandoned, as a share of games with an end report; quick
    quits: abandoned within QUICK_QUIT_SECONDS of an observed start. Repeat players played the
    group REPEAT_GAMES or more times; returning players played it on two or more UTC days.
    """
    ended = [r for r in rows if r['outcome'] in ('finished', 'abandoned') and r['ended_at'] is not None]
    measured = [r for r in ended if r['has_start'] and r['started_at'] is not None and r['ended_at'] >= r['started_at']]
    durations = sorted(r['ended_at'] - r['started_at'] for r in measured if r['outcome'] == 'finished')
    games = collections.Counter()
    days = collections.defaultdict(set)
    for row in rows:
        for identity in identities.get(row['match_id'], ()):
            games[identity] += 1
            days[identity].add(row['day'])
    return {'label': label, 'games': len(rows), 'ended': len(ended), 'measured': len(measured),
            'quits': sum(r['outcome'] == 'abandoned' for r in ended),
            'quick_quits': sum(r['outcome'] == 'abandoned' and r['ended_at'] - r['started_at'] <= QUICK_QUIT_SECONDS
                               for r in measured),
            'players': len(games), 'identified_games': sum(games.values()),
            'repeat_players': sum(n >= REPEAT_GAMES for n in games.values()),
            'returning_players': sum(len(d) >= 2 for d in days.values()),
            'median_finished_seconds': (durations[(len(durations)-1)//2] + durations[len(durations)//2]) / 2
                                       if durations else None, '_ids': set(games)}


def rate(part, whole):
    return part / whole if whole else None


def engagement(rows, identities, key, progression=False):
    """Engagement per label, with a verdict relative to the whole table.

    progression=True is for numbered campaign levels and missions: replaying one is being stuck,
    not liking it, so repeat and returning play are not signals there. Instead `continued_players`
    counts players of a level who also started the next one in the period.

    The baseline pools every row of the table, so a verdict says a mode (or mod, level, mission,
    map) does better or worse than the others, not that it is good in absolute terms. Rows below
    VERDICT_MIN_GAMES games or VERDICT_MIN_PLAYERS identified players get the verdict too_few.
    """
    groups = collections.defaultdict(list)
    for row in rows:
        label = key(row)
        if label is not None:
            groups[str(label)].append(row)
    table = [engagement_row(label, subset, identities) for label, subset in groups.items()]
    ids = {r['label']: r.pop('_ids') for r in table}
    for row in table:
        following = ids.get(str(int(row['label']) + 1)) if progression else None
        row['continued_players'] = len(ids[row['label']] & following) if following is not None else None
    with_next = [r for r in table if r['continued_players'] is not None]
    pooled = {field: sum(r[field] for r in table) for field in
              ('ended', 'measured', 'quits', 'quick_quits', 'players', 'repeat_players', 'returning_players')}
    base = {'quit': rate(pooled['quits'], pooled['ended']),
            'quick_quit': rate(pooled['quick_quits'], pooled['measured']),
            'repeat': rate(pooled['repeat_players'], pooled['players']),
            'returning': rate(pooled['returning_players'], pooled['players']),
            'continued': rate(sum(r['continued_players'] for r in with_next), sum(r['players'] for r in with_next))}
    for row in table:
        mine = {'quit': rate(row['quits'], row['ended']),
                'quick_quit': rate(row['quick_quits'], row['measured']),
                'repeat': rate(row['repeat_players'], row['players']),
                'returning': rate(row['returning_players'], row['players']),
                'continued': rate(row['continued_players'] or 0, row['players']) if row['continued_players'] is not None else None}
        good, bad = [], []
        def above(name, factor):
            return mine[name] is not None and base[name] and mine[name] >= base[name] * factor
        def below(name, factor):
            return mine[name] is not None and base[name] and mine[name] <= base[name] * factor
        if progression:
            if above('continued', 1.2):
                good.append('continued')
            if below('continued', 0.6):
                bad.append('stopped')
        else:
            if above('returning', 1.2):
                good.append('returning')
            if above('repeat', 1.2):
                good.append('repeat')
            if below('returning', 0.6):
                bad.append('not_returning')
        if below('quit', 0.8):
            good.append('fewer_quits')
        if above('quick_quit', 1.5):
            bad.append('quick_quits')
        if above('quit', 1.25):
            bad.append('quits')
        if row['games'] < VERDICT_MIN_GAMES or row['players'] < VERDICT_MIN_PLAYERS:
            verdict = 'too_few'
        else:
            verdict = ('mixed' if good and bad else 'liked' if good else 'disliked' if bad else 'average')
        row.update(verdict=verdict, good=good, bad=bad)
    table.sort(key=(lambda r: int(r['label'])) if progression else (lambda r: (-r['games'], r['label'])))
    return {'baseline': base, 'rows': table}


def empty_player_count(label):
    return dict(label=label, **dict.fromkeys(RUNTIMES, 0), total=0, install_ids=0, sessions=0,
                unidentified_sessions=0)


def clean_map(value):
    return re.split(r'[/\\]', value or '')[-1][:120] or 'Unknown map'


def dunecity_mission(map_name):
    match = DUNECITY_MISSION.fullmatch(clean_map(map_name))
    number = int(match[1]) if match else 0
    return number if 1 <= number <= len(DUNECITY_MISSIONS) else None


def dunecity_campaign(rows, players, identities, today):
    """Games, players and results of the DuneCity Campaign within one period's rows."""
    whole = player_counts(rows, identities, lambda r: 'all').get('all', empty_player_count('all'))
    result = outcomes(rows, players)
    missions = []
    for number, name in enumerate(DUNECITY_MISSIONS, 1):
        subset = [r for r in rows if r['mission'] == number]
        mission = outcomes(subset, players)
        missions.append({'mission': number, 'name': name, 'starts': len(subset),
                         'players': player_counts(subset, identities, lambda r: 'm').get('m', empty_player_count('m'))['total'],
                         'won': mission['human_wins'], 'lost': mission['human_losses'],
                         'exited': mission['exited'], 'missing_end': mission['missing_end']})
    daily = {r['label']: r for r in matrix(rows, lambda r: r['day'])}
    daily_players = player_counts(rows, identities, lambda r: r['day'])
    if rows:   # zero days from the first campaign game in this period
        day = dt.date.fromisoformat(min(r['day'] for r in rows))
        while day <= today:
            key = day.isoformat()
            daily.setdefault(key, dict(label=key, **dict.fromkeys(RUNTIMES, 0), total=0))
            daily_players.setdefault(key, empty_player_count(key))
            day += dt.timedelta(days=1)
    return {'since': iso(min(r['started_at'] for r in rows)) if rows else None, 'games': len(rows),
            'players': whole, 'won': result['human_wins'], 'lost': result['human_losses'],
            'missions': missions, 'daily': sorted(daily.values(), key=lambda r: r['label']),
            'daily_players': sorted(daily_players.values(), key=lambda r: r['label'])}


def display_name(value, fallback='Unknown'):
    # Lobby display names only; control characters never reach the page.
    return re.sub(r'[\x00-\x1f\x7f]', '', str(value or '')).strip()[:32] or fallback


def player_kind(controller, ai_type):
    """What the reporting client said the player was; nothing is inferred from a name."""
    if controller == 'human':
        return 'human'
    if controller in ('ai', 'qbot') or ai_type:
        return 'ai'
    return 'unknown'


def roster(rows):
    """One published participant list: display name, house and what the player was."""
    people = []
    for row in rows:
        kind = player_kind(row['controller'], row['ai_type'])
        name = (AI_NAMES.get(row['ai_type']) if kind == 'ai' else None) or display_name(
            row['player_name'], 'AI' if kind == 'ai' else 'Unknown player')
        people.append({'name': name, 'house': display_name(row['house_name'], ''), 'kind': kind})
    return people


def history_columns(connection):
    """An unmigrated database can lack the player columns; report instead of failing."""
    available = {table: {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')}
                 for table in ('analytics_matches', 'analytics_players')}
    return (HISTORY_MATCH_COLUMNS <= available['analytics_matches']
            and HISTORY_PLAYER_COLUMNS <= available['analytics_players'])


def build_history_page(connection, page, now=None, content_dir=CONTENT_DIR):
    """One page of multiplayer games with at least two human players, newest first.

    The page is read with SQL LIMIT/OFFSET against the whole recorded history, so every
    qualifying game stays reachable regardless of the aggregate periods.  Each stored
    match is one game: only the host reports a multiplayer match and match_id is the
    primary key, so rows are never merged here.  No match ids or player ids are published.
    """
    now = int(time.time()) if now is None else now
    page = max(1, min(int(page), HISTORY_MAX_PAGE))
    mod_label = mod_labeller(content_dir)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    try:
        usable = history_columns(connection)
        total, games = 0, []
        if usable:
            total = connection.execute('SELECT COUNT(*) ' + HISTORY_WHERE, (now,)).fetchone()[0]
            rows = connection.execute(
                '''SELECT m.match_id, m.started_at, m.map_name, m.mod_name, m.game_version '''
                + HISTORY_WHERE + ''' ORDER BY m.started_at DESC, m.match_id DESC LIMIT ? OFFSET ?''',
                (now, HISTORY_PAGE_SIZE, (page - 1) * HISTORY_PAGE_SIZE)).fetchall()
            rosters = collections.defaultdict(list)
            if rows:
                placeholders = ','.join('?' * len(rows))
                for player in connection.execute(
                        '''SELECT match_id, player_name, house_name, controller, ai_type
                        FROM analytics_players WHERE match_id IN (''' + placeholders + ')'
                        ' ORDER BY match_id, slot', [row['match_id'] for row in rows]):
                    rosters[player['match_id']].append(player)
            for row in rows:
                people = roster(rosters.get(row['match_id'], []))
                games.append({'started': iso(row['started_at']), 'map': clean_map(row['map_name']),
                              'mod': mod_label(row['mod_name']),
                              'version': display_name(row['game_version'], '') or None,
                              'humans': sum(p['kind'] == 'human' for p in people),
                              'ai': sum(p['kind'] == 'ai' for p in people),
                              'unknown': sum(p['kind'] == 'unknown' for p in people),
                              'players': people})
    finally:
        connection.rollback()
    return {'schema': 1, 'generated': iso(now), 'page': page, 'page_size': HISTORY_PAGE_SIZE,
            'pages': max(1, -(-total // HISTORY_PAGE_SIZE)), 'total': total,
            'available': usable, 'games': games}


def build_report(connection, now=None, content_dir=CONTENT_DIR):
    now = int(time.time()) if now is None else now
    mod_label = mod_labeller(content_dir)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    rows = [dict(r) for r in connection.execute('''SELECT match_id, client_runtime, started_at, ended_at,
        outcome, game_type, map_name, mod_name, start_json IS NOT NULL AS has_start
        FROM analytics_matches WHERE started_at IS NOT NULL AND started_at <= ?''', (now,))]
    for row in rows:   # UTC day and month labels, formatted once for every chart and period
        row['day'] = iso(row['started_at'])[:10]
        row['month'] = row['day'][:7]
        row['mission'] = dunecity_mission(row['map_name'])
        row['mode'] = 'dunecity_campaign' if row['mission'] else (row['game_type'] or 'unknown')
        row['mod'] = mod_label(row['mod_name'])
    columns = {row[1] for row in connection.execute('PRAGMA table_info(analytics_players)')}
    players = collections.defaultdict(list)
    for row in connection.execute('''SELECT match_id, house_slot, team, controller,
        ai_type, ai_difficulty, ai_support, result, '''
            + ('player_name' if 'player_name' in columns else 'NULL AS player_name')
            + (', install_key' if 'install_key' in columns else '') + ' FROM analytics_players'):
        players[row['match_id']].append(dict(row))
    connection.rollback()
    identities = player_identities(players, columns)
    first = min((r['started_at'] for r in rows), default=None)
    known_first = min((r['started_at'] for r in rows if runtime(r['client_runtime']) != 'unknown'), default=None)
    report = {'schema': 1, 'generated': iso(now), 'tracking_since': iso(first) if first else None,
              'platform_since': iso(known_first) if known_first else None, 'periods': {}}
    for name, cutoff in [('day', now-86400), ('month', now-30*86400), ('all', first or now)]:
        selected = [r for r in rows if r['started_at'] >= cutoff]
        period = {'since': iso(max(cutoff, first or now)), 'total': len(selected),
                  'runtime': matrix(selected, lambda r: runtime(r['client_runtime'])),
                  'modes': matrix(selected, lambda r: r['mode']),
                  'mods': matrix(selected, lambda r: r['mod']),
                  'levels': matrix([r for r in selected if r['mode'] == 'campaign'], lambda r: level(r['map_name'])),
                  'maps': matrix([r for r in selected if r['mode'] in ('single_custom','multiplayer')], lambda r: clean_map(r['map_name']))[:MAP_LIMIT],
                  'daily': matrix(selected, lambda r: r['day']),
                  'monthly': matrix(selected, lambda r: r['month']),
                  'outcomes': [], 'lengths': [], 'bots': bot_stats(selected, players),
                  'players': player_counts(selected, identities, lambda r: 'period').get('period', empty_player_count('period'))}
        daily_players = player_counts(selected, identities, lambda r: r['day'])
        monthly_players = player_counts(selected, identities, lambda r: r['month'])
        # Include zero-activity days/months within the observed tracking period.
        begin = dt.datetime.fromtimestamp(max(cutoff, first or now), UTC).date()
        end = dt.datetime.fromtimestamp(now, UTC).date()
        daily = {r['label']: r for r in period['daily']}
        months = {r['label']: r for r in period['monthly']}
        day = begin
        while day <= end:
            day_key, month_key = day.isoformat(), day.strftime('%Y-%m')
            daily.setdefault(day_key, dict(label=day_key, **dict.fromkeys(RUNTIMES, 0), total=0))
            months.setdefault(month_key, dict(label=month_key, **dict.fromkeys(RUNTIMES, 0), total=0))
            daily_players.setdefault(day_key, empty_player_count(day_key))
            monthly_players.setdefault(month_key, empty_player_count(month_key))
            day += dt.timedelta(days=1)
        period['daily'], period['monthly'] = list(daily.values()), list(months.values())
        period['daily'].sort(key=lambda r:r['label'])
        period['monthly'].sort(key=lambda r:r['label'])
        period['daily_players'] = sorted(daily_players.values(), key=lambda r: r['label'])
        period['monthly_players'] = sorted(monthly_players.values(), key=lambda r: r['label'])
        # What the identified players play: distinct players per mode, mod and campaign level,
        # and how many play each combination of modes and of mods.
        period['players_by'] = {
            'modes': players_by(selected, identities, lambda r: r['mode']),
            'mods': players_by(selected, identities, lambda r: r['mod']),
            'levels': players_by([r for r in selected if r['mode'] == 'campaign'], identities, lambda r: level(r['map_name']))}
        period['player_mix'] = {'modes': player_mix(selected, identities, lambda r: r['mode'], MODE_ORDER),
                                'mods': player_mix(selected, identities, lambda r: r['mod'], SHIPPED_MODS)}
        top_maps = {r['label'] for r in period['maps']}
        custom = [r for r in selected if r['mode'] in ('single_custom', 'multiplayer')]
        period['engagement'] = {
            'modes': engagement(selected, identities, lambda r: r['mode']),
            'mods': engagement(selected, identities, lambda r: r['mod']),
            'levels': engagement([r for r in selected if r['mode'] == 'campaign'], identities,
                                 lambda r: level(r['map_name']), progression=True),
            'dunecity_missions': engagement(selected, identities, lambda r: r['mission'], progression=True),
            'maps': engagement(custom, identities, lambda r: clean_map(r['map_name']) if clean_map(r['map_name']) in top_maps else None)}
        period['dunecity_campaign'] = dunecity_campaign([r for r in selected if r['mission']], players, identities, end)
        for rt in RUNTIMES:
            cohort = [r for r in selected if runtime(r['client_runtime']) == rt]
            period['outcomes'].append(dict(runtime=rt, **outcomes(cohort, players)))
            for label, subset in [('Campaign', [r for r in cohort if r['mode']=='campaign']),
                                  ('Campaign level 1', [r for r in cohort if r['mode']=='campaign' and level(r['map_name'])==1]),
                                  ('DuneCity Campaign', [r for r in cohort if r['mode']=='dunecity_campaign']),
                                  ('Other modes', [r for r in cohort if r['mode'] not in ('campaign','dunecity_campaign')])]:
                period['lengths'].append(dict(runtime=rt, label=label, **lengths(subset, players)))
        report['periods'][name] = period
    return report


def arguments(argv):
    """[database] [--history-page N]. The page number is the only caller-chosen value."""
    rest = list(argv)
    db = rest.pop(0) if rest and not rest[0].startswith('--') else '/var/www/data/games.sqlite'
    if not rest:
        return db, None
    if len(rest) != 2 or rest[0] != '--history-page' or not re.fullmatch(r'[1-9][0-9]{0,6}', rest[1]):
        raise SystemExit('usage: usage_stats.py [database] [--history-page N]')
    return db, int(rest[1])


if __name__ == '__main__':
    db, page = arguments(sys.argv[1:])
    with sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5) as connection:
        report = build_report(connection) if page is None else build_history_page(connection, page)
        json.dump(report, sys.stdout, separators=(',', ':'))
