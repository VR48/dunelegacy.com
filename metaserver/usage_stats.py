#!/usr/bin/env python3
"""Public aggregates and one page of multiplayer history.

Read the live SQLite database without changing it.  Without arguments the output is the
aggregate report exactly as before; --history-page N returns that page of the multiplayer
history and nothing else.
"""
import collections
import datetime as dt
import json
import re
import sqlite3
import sys
import time

UTC = dt.timezone.utc
RUNTIMES = ('browser', 'native', 'unknown')
AI_NAMES = {'qbot': 'QuantBot', 'campaign': 'Campaign AI', 'classic': 'Classic AI',
            'smartbot': 'SmartBot', 'mentat': 'Mentat'}
HISTORY_PAGE_SIZE = 20
HISTORY_MAX_PAGE = 10 ** 7
# A game qualifies only on recorded controllers: two or more player rows that the reporting
# client called human.  Unknown or missing controllers never qualify a game.
HISTORY_WHERE = '''FROM analytics_matches AS m WHERE m.game_type = 'multiplayer'
    AND m.started_at IS NOT NULL AND m.started_at <= ?
    AND (SELECT COUNT(*) FROM analytics_players AS p
         WHERE p.match_id = m.match_id AND p.controller = 'human') >= 2'''
HISTORY_MATCH_COLUMNS = {'match_id', 'started_at', 'game_type', 'map_name', 'mod_name', 'game_version'}
HISTORY_PLAYER_COLUMNS = {'match_id', 'slot', 'player_name', 'house_name', 'controller', 'ai_type'}


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
            mode = 'Campaign' if row['game_type'] == 'campaign' else 'Other modes'
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


def clean_map(value):
    return re.split(r'[/\\]', value or '')[-1][:120] or 'Unknown map'


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


def build_history_page(connection, page, now=None):
    """One page of multiplayer games with at least two human players, newest first.

    The page is read with SQL LIMIT/OFFSET against the whole recorded history, so every
    qualifying game stays reachable regardless of the aggregate periods.  Each stored
    match is one game: only the host reports a multiplayer match and match_id is the
    primary key, so rows are never merged here.  No match ids or player ids are published.
    """
    now = int(time.time()) if now is None else now
    page = max(1, min(int(page), HISTORY_MAX_PAGE))
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
                              'mod': (row['mod_name'] or 'unknown')[:80],
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


def build_report(connection, now=None):
    now = int(time.time()) if now is None else now
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    rows = [dict(r) for r in connection.execute('''SELECT match_id, client_runtime, started_at, ended_at,
        outcome, game_type, map_name, mod_name, start_json IS NOT NULL AS has_start
        FROM analytics_matches WHERE started_at IS NOT NULL AND started_at <= ?''', (now,))]
    players = collections.defaultdict(list)
    for row in connection.execute('''SELECT match_id, house_slot, team, controller,
        ai_type, ai_difficulty, ai_support, result FROM analytics_players'''):
        players[row['match_id']].append(dict(row))
    connection.rollback()
    first = min((r['started_at'] for r in rows), default=None)
    known_first = min((r['started_at'] for r in rows if runtime(r['client_runtime']) != 'unknown'), default=None)
    report = {'schema': 1, 'generated': iso(now), 'tracking_since': iso(first) if first else None,
              'platform_since': iso(known_first) if known_first else None, 'periods': {}}
    for name, cutoff in [('day', now-86400), ('month', now-30*86400), ('all', first or now)]:
        selected = [r for r in rows if r['started_at'] >= cutoff]
        period = {'since': iso(max(cutoff, first or now)), 'total': len(selected),
                  'runtime': matrix(selected, lambda r: runtime(r['client_runtime'])),
                  'modes': matrix(selected, lambda r: r['game_type'] or 'unknown'),
                  'mods': matrix(selected, lambda r: (r['mod_name'] or 'unknown')[:80]),
                  'levels': matrix([r for r in selected if r['game_type'] == 'campaign'], lambda r: level(r['map_name'])),
                  'maps': matrix([r for r in selected if r['game_type'] in ('single_custom','multiplayer')], lambda r: clean_map(r['map_name'])),
                  'daily': matrix(selected, lambda r: iso(r['started_at'])[:10]),
                  'monthly': matrix(selected, lambda r: iso(r['started_at'])[:7]),
                  'outcomes': [], 'lengths': [], 'bots': bot_stats(selected, players)}
        # Include zero-activity days/months within the observed tracking period.
        begin = dt.datetime.fromtimestamp(max(cutoff, first or now), UTC).date()
        end = dt.datetime.fromtimestamp(now, UTC).date()
        daily = {r['label']: r for r in period['daily']}
        months = {r['label']: r for r in period['monthly']}
        day = begin
        while day <= end:
            day_key, month_key = day.isoformat(), day.strftime('%Y-%m')
            daily.setdefault(day_key, dict(label=day_key, browser=0, native=0, unknown=0, total=0))
            months.setdefault(month_key, dict(label=month_key, browser=0, native=0, unknown=0, total=0))
            day += dt.timedelta(days=1)
        period['daily'], period['monthly'] = list(daily.values()), list(months.values())
        period['daily'].sort(key=lambda r:r['label'])
        period['monthly'].sort(key=lambda r:r['label'])
        for rt in RUNTIMES:
            cohort = [r for r in selected if runtime(r['client_runtime']) == rt]
            period['outcomes'].append(dict(runtime=rt, **outcomes(cohort, players)))
            for label, subset in [('Campaign', [r for r in cohort if r['game_type']=='campaign']),
                                  ('Campaign level 1', [r for r in cohort if r['game_type']=='campaign' and level(r['map_name'])==1]),
                                  ('Other modes', [r for r in cohort if r['game_type']!='campaign'])]:
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
