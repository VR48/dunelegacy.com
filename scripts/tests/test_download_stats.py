import datetime as dt
import importlib.util
import unittest
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('stats',Path(__file__).parents[1]/'update-download-stats.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class StatsTests(unittest.TestCase):
    def test_window_excludes_old_lifetime_and_includes_new(self):
        assets={'1':{'created':'2025-01-01T00:00:00Z','count':1000},'2':{'created':'2026-09-10T00:00:00Z','count':12}}
        start=m.stamp('2026-09-01T00:00:00Z');end=m.stamp('2026-09-11T00:00:00Z')
        self.assertEqual(m.gh_window(assets,[],start,end),12)
        self.assertEqual(m.gh_window(assets,[{'at':'2026-09-02T00:00:00Z','counts':{'1':990,'2':3}}],start,end),22)
    def test_removed_assets_retained_and_day_needs_baseline(self):
        state={'assets':{'1':{'created':'2025-01-01T00:00:00Z','count':55,'platform':'windows'}}}
        sf={'total':100,'downloads':[['2026-09-11 00:00:00',5]]}
        first=m.compile_stats(state,[],sf,m.stamp('2026-09-11T12:00:00Z'))
        self.assertIsNone(first['day']);self.assertEqual(first['total']['total'],155)
        next_sf={'total':109,'downloads':[['2026-09-11 00:00:00',5],['2026-09-12 00:00:00',9]]}
        second=m.compile_stats(state,[],next_sf,m.stamp('2026-09-12T12:00:00Z'))
        self.assertEqual(second['day']['total'],9)
        self.assertIsNone(second['years'][1]['github'])
    def test_pagination_follows_all_pages(self):
        with patch.object(m, 'request_json', side_effect=[([{'id':1}], '<https://api.github.com/page2>; rel="next"'), ([{'id':2}], '')]) as request:
            self.assertEqual(m.github_releases(), [{'id':1},{'id':2}])
            self.assertEqual(request.call_count, 2)

    def test_explicit_sourceforge_periods_override_coarse_timeline(self):
        sf={'total':1000,'downloads':[['2026-09-01 00:00:00',200]],'periods':{'2026-08-13':556,'2026-01-01':700},'years':{str(y):700 for y in range(2022,2027)}}
        result=m.compile_stats({},[],sf,m.stamp('2026-09-11T12:00:00Z'))
        self.assertEqual(result['month']['sourceforge'],556)
        self.assertEqual(result['year']['sourceforge'],700)

    def test_monthly_history_does_not_use_current_lifetime_counts(self):
        assets={'old':{'created':'2025-01-01T00:00:00Z','count':9999},
                'aug':{'created':'2026-08-10T00:00:00Z','count':8888}}
        history=[{'at':'2026-08-01T00:00:00Z','counts':{'old':100}},
                 {'at':'2026-08-31T23:00:00Z','counts':{'old':110,'aug':20}},
                 {'at':'2026-09-01T00:00:00Z','counts':{'old':115,'aug':22}}]
        now=m.stamp('2026-09-14T00:00:00Z')
        sf={'months':{start.strftime('%Y-%m'):50 for start,end in m.calendar_months(now)}}
        months=m.monthly_stats(assets,history,sf,now)
        self.assertEqual(len(months),12)
        self.assertEqual(months[0]['month'],'2025-10')
        self.assertEqual(months[-2]['github'],30)
        self.assertEqual(months[-2]['total'],80)
        self.assertIsNone(months[0]['github'])
        self.assertEqual(months[0]['total'],50)
        self.assertTrue(months[-1]['partial_month'])
        self.assertFalse(months[-2]['partial_month'])

    def test_calendar_months_cross_year_and_leap_day(self):
        months=list(m.calendar_months(m.stamp('2024-02-29T12:00:00Z')))
        self.assertEqual(months[0][0].strftime('%Y-%m-%d'),'2023-03-01')
        self.assertEqual(months[-1][1].strftime('%Y-%m-%d'),'2024-03-01')

    def test_daily_total_survives_a_gap_around_24_hours(self):
        state={'assets':{'old':{'created':'2025-01-01T00:00:00Z','count':120,'platform':'windows'}},
               'snapshots':[{'at':'2026-09-12T06:00:00Z','counts':{'old':100},'sourceforge':500},
                            {'at':'2026-09-12T12:00:00Z','counts':{'old':110},'sourceforge':510}]}
        result=m.compile_stats(state,[],{'total':540,'downloads':[]},m.stamp('2026-09-13T08:00:00Z'))
        self.assertEqual(result['day']['total'],60)
        self.assertEqual(result['day']['since'],'2026-09-12T06:00:00Z')
        self.assertEqual(result['day']['window_seconds'],26*3600)

    def test_invalid_source_does_not_publish_zero(self):
        with self.assertRaises(ValueError):m.compile_stats({},[],{},dt.datetime.now(m.UTC))

if __name__=='__main__':unittest.main()
