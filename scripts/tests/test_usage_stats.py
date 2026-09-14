import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

spec=importlib.util.spec_from_file_location('usage',Path(__file__).parents[2]/'metaserver/usage_stats.py')
usage=importlib.util.module_from_spec(spec);spec.loader.exec_module(usage)
NOW=1789430400

class UsageTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('''CREATE TABLE analytics_matches(match_id TEXT,client_runtime TEXT,started_at INTEGER,ended_at INTEGER,outcome TEXT,game_type TEXT,map_name TEXT,mod_name TEXT,start_json TEXT);
        CREATE TABLE analytics_players(match_id TEXT,house_slot INTEGER,team INTEGER,controller TEXT,ai_type TEXT,ai_difficulty TEXT,ai_support INTEGER,result TEXT);''')
    def match(self,mid,rt='browser',start=NOW-100,end=NOW-40,outcome='abandoned',mode='campaign',map_name='SCENA001',has_start=True):
        self.db.execute('INSERT INTO analytics_matches VALUES(?,?,?,?,?,?,?,?,?)',(mid,rt,start,end,outcome,mode,map_name,'vanilla','{"private":"NEVER_PUBLISH_ME"}' if has_start else None))
    def player(self,mid,house=1,team=1,controller='human',ai=None,support=0,result='alive'):
        self.db.execute('INSERT INTO analytics_players VALUES(?,?,?,?,?,?,?,?)',(mid,house,team,controller,ai,'easy' if ai else None,support,result))
    def report(self):
        self.db.commit();return usage.build_report(self.db,NOW)
    def test_real_campaign_mapping(self):
        expected=[1,2,2,2,3,3,3,4,4,4,5,5,5,6,6,6,7,7,7,8,8,9]
        self.assertEqual([usage.level(f'SCENH{n:03}') for n in range(1,23)],expected)
        self.assertIsNone(usage.level('custom map 001'))
    def test_periods_unknown_runtime_and_private_fields(self):
        self.match('recent');self.player('recent')
        self.match('old',rt='unknown',start=NOW-31*86400,end=None)
        self.match('edge',rt='native',start=NOW-86400,end=None)
        self.match('future',start=NOW+100,end=None)
        r=self.report()
        self.assertEqual(r['periods']['day']['total'],2)
        self.assertEqual(r['periods']['month']['total'],2)
        self.assertEqual(r['periods']['all']['total'],3)
        self.assertEqual(sum(x['total'] for x in r['periods']['all']['runtime']),3)
        self.assertNotIn('NEVER_PUBLISH_ME',json.dumps(r))
        self.assertNotIn('match_id',json.dumps(r))
        self.assertEqual(r['periods']['day']['lengths'][0]['quick_exits'],1)
        with self.assertRaises(sqlite3.OperationalError):self.db.execute("DELETE FROM analytics_matches")
    def test_bot_deduplication_and_relationships(self):
        self.match('game');self.player('game')
        self.player('game',controller='qbot',ai='qbot',support=1)
        self.player('game',controller='qbot',ai='qbot',support=1)
        self.player('game',house=2,team=1,controller='ai',ai='classic')
        self.player('game',house=3,team=2,controller='qbot',ai='qbot')
        bots=self.report()['periods']['day']['bots']
        self.assertEqual(len(bots['ai']),3)
        self.assertTrue(all(r['browser']==1 for r in bots['ai']))
        self.assertEqual(next(r for r in bots['assistance'] if r['label']=='Support AI')['browser'],1)
    def test_end_only_and_unreported_are_not_measured_or_losses(self):
        self.match('exit');self.player('exit',result='winner')
        self.match('endonly',has_start=False,outcome='finished');self.player('endonly',result='winner')
        self.match('open',end=None,outcome='started');self.player('open')
        self.match('loss',start=NOW-1000,end=NOW-100,outcome='finished',map_name='SCENO022');self.player('loss',result='defeated')
        p=self.report()['periods']['day'];r=p['outcomes'][0]
        self.assertEqual((r['human_wins'],r['human_losses'],r['exited'],r['missing_end']),(1,1,1,1))
        lengths=p['lengths'][0]
        self.assertEqual(lengths['measured'],2)
        self.assertEqual(lengths['average_seconds'],480)
        self.assertEqual(lengths['longest_outcome'],'Human loss')
    def test_empty_database(self):
        r=self.report();self.assertIsNone(r['tracking_since']);self.assertEqual(r['periods']['all']['total'],0)

if __name__=='__main__':unittest.main()
