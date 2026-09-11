#!/usr/bin/env python3
"""Collect complete release inventories; publish honest download lower bounds."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import urllib.request

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'website/data'
STATE = ROOT / 'analytics/download-state.json'


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def request_json(url, token=None):
    headers = {'User-Agent': 'DuneLegacy-download-statistics', 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                return json.load(response), response.headers.get('Link', '')
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def github_releases():
    url = 'https://api.github.com/repos/VR48/dunecity/releases?per_page=100'
    releases = []
    while url:
        page, links = request_json(url, os.environ.get('GH_TOKEN'))
        if not isinstance(page, list):
            raise ValueError('Invalid GitHub release response')
        releases.extend(page)
        url = next((part.split(';')[0].strip()[1:-1] for part in links.split(',') if 'rel="next"' in part), None)
    return releases


def platform(name):
    name = name.lower()
    if 'windows' in name: return 'windows'
    if 'macos' in name: return 'macos'
    if 'linux' in name or name.endswith('.deb'): return 'linux'
    if name.endswith('.apk'): return 'android'
    return 'other'


def gh_window(assets, history, start, end):
    """Never assign an old asset's lifetime count to a recent interval."""
    baselines = [s for s in history if start <= stamp(s['at']) <= end]
    baseline = min(baselines, key=lambda s: s['at']) if baselines else None
    total = 0
    for key, asset in assets.items():
        if start <= stamp(asset['created']) <= end:
            total += asset['count']
        elif baseline and key in baseline['counts']:
            total += max(0, asset['count'] - baseline['counts'][key])
    return total


def compile_stats(state, releases, sf, now):
    if not isinstance(sf.get('total'), int) or not isinstance(sf.get('downloads'), list):
        raise ValueError('Invalid SourceForge response; retain last good publication')
    assets = state.setdefault('assets', {})
    for release in releases:
        for a in release.get('assets', []):
            key = str(a['id'])
            assets[key] = {'created': a['created_at'], 'count': max(a['download_count'], assets.get(key, {}).get('count', 0)), 'platform': platform(a['name'])}
    history = state.setdefault('snapshots', [])
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    year_start = midnight.replace(month=1, day=1)
    month_start = midnight - dt.timedelta(days=29)
    def period(start):
        g = gh_window(assets, history, start, now)
        s = sf['periods'][start.date().isoformat()] if 'periods' in sf else sum(n for date, n in sf['downloads'] if start.date().isoformat() <= date[:10] <= now.date().isoformat())
        return {'github': g, 'sourceforge': s, 'total': g+s, 'since': start.isoformat(), 'lower_bound': True}
    day_start = now - dt.timedelta(hours=24)
    baseline = next(iter(sorted((s for s in history if day_start <= stamp(s['at']) <= day_start + dt.timedelta(hours=1, minutes=30)), key=lambda s:s['at'])), None)
    day = None
    if baseline:
        g = gh_window(assets, [baseline], stamp(baseline['at']), now)
        s = max(0, sf['total'] - baseline['sourceforge'])
        day = {'github': g, 'sourceforge': s, 'total': g+s, 'since': baseline['at'], 'lower_bound': True}
    years = []
    for year in range(now.year, now.year-5, -1):
        s = sf['years'][str(year)] if 'years' in sf else sum(n for date,n in sf['downloads'] if date.startswith(str(year)))
        g = period(year_start)['github'] if year == now.year else None
        if year != now.year:
            start = midnight.replace(year=year,month=1,day=1)
            end = start.replace(year=year+1)
            recorded = [sample for sample in history if start <= stamp(sample['at']) < end]
            if recorded:
                last = max(recorded,key=lambda sample:sample['at'])
                historical = {key:dict(assets[key],count=count) for key,count in last['counts'].items() if key in assets}
                g = gh_window(historical,history,start,stamp(last['at']))
        years.append({'year':year, 'github':g, 'sourceforge':s, 'total':None if g is None else g+s})
    counts = {key:a['count'] for key,a in assets.items()}
    history.append({'at':now.isoformat(), 'counts':counts, 'sourceforge':sf['total']})
    # Hourly detail for two days; one sample per UTC day for long-term intervals.
    older = {}
    recent = []
    for sample in history:
        age = now-stamp(sample['at'])
        if age <= dt.timedelta(days=2): recent.append(sample)
        elif age <= dt.timedelta(days=400): older.setdefault(sample['at'][:10], sample)
    state['snapshots'] = list(older.values()) + recent
    gtotal = sum(counts.values())
    platforms = {p:sum(a['count'] for a in assets.values() if a['platform']==p) for p in ['windows','macos','linux','android']}
    return {'generated':now.isoformat(), 'total':{'github':gtotal,'sourceforge':sf['total'],'total':gtotal+sf['total'],'lower_bound':True}, 'year':period(year_start), 'month':period(month_start), 'day':day, 'years':years, 'platforms':platforms}


def main():
    now = dt.datetime.now(UTC)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    releases = github_releases()
    # SourceForge coarsens long-range timeline buckets. Query each interval's
    # total directly; slicing an all-time timeline understates recent periods.
    today = now.date().isoformat()
    month = (now.date()-dt.timedelta(days=29)).isoformat()
    ranges = {'total':('2000-01-01',today), 'month':(month,today)}
    ranges.update({str(y):(f'{y}-01-01', today if y == now.year else f'{y}-12-31') for y in range(now.year,now.year-5,-1)})
    def fetch_range(item):
        key,(start,end)=item
        result,_=request_json(f'https://sourceforge.net/projects/dunelegacy/files/stats/json?start_date={start}&end_date={end}')
        if not isinstance(result.get('total'),int): raise ValueError('Invalid SourceForge period')
        return key,result['total']
    with ThreadPoolExecutor(max_workers=3) as pool:
        totals=dict(pool.map(fetch_range,ranges.items()))
    sf={'total':totals['total'], 'downloads':[], 'periods':{month:totals['month'],f'{now.year}-01-01':totals[str(now.year)]}, 'years':{str(y):totals[str(y)] for y in range(now.year,now.year-5,-1)}}
    stats = compile_stats(state, releases, sf, now)
    STATE.parent.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    for path, value in [(STATE,state),(DATA/'download-stats.json',stats)]:
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(value, separators=(',', ':'))+'\n')
        temp.replace(path)
    print('Collected',len(releases),'releases;',stats['total']['total'],'known file downloads')

if __name__ == '__main__':
    main()
