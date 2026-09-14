#!/usr/bin/env python3
"""Public aggregates only. Read the live SQLite database without changing it."""
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


if __name__ == '__main__':
    db = sys.argv[1] if len(sys.argv) > 1 else '/var/www/data/games.sqlite'
    with sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5) as connection:
        json.dump(build_report(connection), sys.stdout, separators=(',', ':'))
