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
        self.db.executescript('''CREATE TABLE analytics_matches(match_id TEXT,client_runtime TEXT,started_at INTEGER,ended_at INTEGER,outcome TEXT,game_type TEXT,map_name TEXT,mod_name TEXT,game_version TEXT,start_json TEXT);
        CREATE TABLE analytics_players(match_id TEXT,slot INTEGER,player_name TEXT,house_name TEXT,house_slot INTEGER,team INTEGER,controller TEXT,ai_type TEXT,ai_difficulty TEXT,ai_support INTEGER,result TEXT);''')
        self.slots={}
    def match(self,mid,rt='browser',start=NOW-100,end=NOW-40,outcome='abandoned',mode='campaign',map_name='SCENA001',has_start=True,mod='vanilla',version='1.0.787'):
        self.db.execute('INSERT INTO analytics_matches VALUES(?,?,?,?,?,?,?,?,?,?)',(mid,rt,start,end,outcome,mode,map_name,mod,version,'{"private":"NEVER_PUBLISH_ME"}' if has_start else None))
    def player(self,mid,house=1,team=1,controller='human',ai=None,support=0,result='alive',name=None,house_name=None):
        slot=self.slots.get(mid,0);self.slots[mid]=slot+1
        self.db.execute('INSERT INTO analytics_players VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (mid,slot,name if name is not None else 'Player%d'%slot,house_name,house,team,controller,ai,'easy' if ai else None,support,result))
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

    def history(self,page=1):
        self.db.commit()
        return usage.build_history_page(self.db,page,NOW)

    def test_history_requires_two_confirmed_humans(self):
        for mid,controllers in [('solo',['human','qbot']),('bots',['ai','qbot']),
                ('legacy',['unknown','unknown']),('spectator',['human','spectator']),
                ('shared',['human','human','qbot'])]:
            self.match(mid,mode='multiplayer',map_name=mid)
            for controller in controllers:self.player(mid,controller=controller)
        self.match('offline',mode='single_custom')
        self.player('offline');self.player('offline')
        for mid,start in [('future',NOW+1),('undated',None)]:
            self.match(mid,mode='multiplayer',start=start)
            self.player(mid);self.player(mid)
        result=self.history()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['games'][0]['map'],'shared')
        self.assertEqual(result['games'][0]['humans'],2)
        self.assertEqual(result['games'][0]['ai'],1)

    def test_history_pagination_and_all_time(self):
        for i in range(45):
            mid=f'm{i:02}'
            self.match(mid,mode='multiplayer',start=NOW-40*86400-i,map_name=mid)
            self.player(mid);self.player(mid)
        pages=[self.history(page) for page in range(1,5)]
        self.assertEqual([len(p['games']) for p in pages],[20,20,5,0])
        self.assertTrue(all(p['total']==45 and p['pages']==3 for p in pages))
        self.assertEqual([g['map'] for p in pages for g in p['games']],
                         [f'm{i:02}' for i in range(45)])

    def test_history_metadata_and_separate_restarts(self):
        # Identical rosters and timestamps do not merge two distinct games.
        for mid in ['first','second']:
            self.match(mid,mode='multiplayer',map_name='/private/maps/Basin',mod='dunecity')
            self.player(mid,name='Alice');self.player(mid,name='Bob')
            self.player(mid,name='Bot',controller='qbot',ai='qbot')
        result=self.history()
        self.assertEqual(result['total'],2)
        game=result['games'][0]
        self.assertEqual((game['map'],game['mod'],game['version']),('Basin','dunecity','1.0.787'))
        self.assertEqual([p['name'] for p in game['players']],['Alice','Bob','QuantBot'])
        self.assertEqual([p['kind'] for p in game['players']],['human','human','ai'])
        encoded=json.dumps(result)
        for private in ['match_id','player_id','NEVER_PUBLISH_ME','/private/']:
            self.assertNotIn(private,encoded)
        with self.assertRaises(sqlite3.OperationalError):self.db.execute('DELETE FROM analytics_matches')

    def test_history_ties_are_stable(self):
        for mid in ['a','c','b']:
            self.match(mid,mode='multiplayer',map_name=mid)
            self.player(mid);self.player(mid)
        self.assertEqual([g['map'] for g in self.history()['games']],['c','b','a'])

    def test_history_empty_and_older_schema(self):
        result=self.history()
        self.assertEqual((result['total'],result['pages'],result['games']),(0,1,[]))
        old=sqlite3.connect(':memory:')
        old.execute('CREATE TABLE analytics_matches(match_id TEXT)')
        result=usage.build_history_page(old,1,NOW)
        self.assertFalse(result['available'])
        self.assertEqual(result['games'],[])
        old.close()

    def test_history_has_no_ten_thousand_game_cutoff(self):
        self.db.executemany('INSERT INTO analytics_matches(match_id,started_at,game_type,map_name) VALUES(?,?,?,?)',
            [(str(i),NOW-i,'multiplayer',str(i)) for i in range(10021)])
        self.db.executemany('INSERT INTO analytics_players(match_id,slot,controller) VALUES(?,?,?)',
            [(str(i),slot,'human') for i in range(10021) for slot in range(2)])
        self.db.execute('CREATE INDEX test_players_match ON analytics_players(match_id)')
        result=self.history(502)
        self.assertEqual(result['total'],10021)
        self.assertEqual([g['map'] for g in result['games']],['10020'])

    def test_cli_aggregate_compatibility_and_page_validation(self):
        self.assertEqual(usage.arguments([]),('/var/www/data/games.sqlite',None))
        self.assertEqual(usage.arguments(['/tmp/test.sqlite']),('/tmp/test.sqlite',None))
        self.assertEqual(usage.arguments(['--history-page','2']),('/var/www/data/games.sqlite',2))
        for page in ['0','-1','1.5','abc','1;SELECT','10000000']:
            with self.assertRaises(SystemExit):usage.arguments(['--history-page',page])

if __name__=='__main__':unittest.main()
