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
        self.db.execute('INSERT INTO analytics_players(match_id,slot,player_name,house_name,house_slot,team,controller,ai_type,ai_difficulty,ai_support,result) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
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
    def test_phone_runtimes_are_counted_apart_from_desktop(self):
        self.match('desktop',rt='native');self.match('android',rt='android')
        self.match('iphone',rt='ios');self.match('ipad',rt='ios')
        p=self.report()['periods']['day']
        runtime={r['label']:r['total'] for r in p['runtime']}
        self.assertEqual(runtime,{'native':1,'android':1,'ios':2})
        today=next(d for d in p['daily'] if d['total'])   # NOW is midnight: the games are on the day before
        self.assertEqual((today['native'],today['android'],today['ios'],today['unknown'],today['total']),(1,1,2,0,4))
        self.assertEqual({r['runtime']:r['sessions'] for r in p['outcomes']}['ios'],2)
        # Days without games still carry every platform column for the page's chart.
        self.assertTrue(all(set(usage.RUNTIMES)<=set(d) for d in self.report()['periods']['all']['daily']))
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

    def test_players_by_name_without_shared_defaults(self):
        self.match('browser',rt='browser');self.player('browser',name='Player')
        self.match('desk1',rt='native');self.player('desk1',name='Alice')
        self.match('desk2',rt='native');self.player('desk2',name=' alice ')
        self.match('generic',rt='native');self.player('generic',name='User')
        self.match('phone',rt='android');self.player('phone',name='ALICE')
        self.match('mp',rt='native',mode='multiplayer');self.player('mp',name='Bob');self.player('mp',name='Carol')
        self.player('mp',name='Dave',controller='qbot',ai='qbot');self.player('mp',name='Erin',controller='unknown')
        self.match('empty',rt='ios')
        report=self.report();players=report['periods']['day']['players']
        self.assertEqual((players['total'],players['native'],players['android'],players['browser'],players['ios']),(3,3,1,0,0))
        self.assertEqual((players['sessions'],players['unidentified_sessions'],players['install_ids']),(7,3,0))
        encoded=json.dumps(report)
        for name in ['Alice','alice','Bob','Carol','name:']:self.assertNotIn(name,encoded)

    def test_daily_and_monthly_players_fill_tracked_days(self):
        self.match('old',rt='native',start=NOW-3*86400);self.player('old',name='Alice')
        self.match('new1',rt='native',start=NOW-100);self.player('new1',name='Alice')
        self.match('new2',rt='browser',start=NOW-90);self.player('new2',name='Bob')
        self.match('new3',rt='browser',start=NOW-80);self.player('new3',name='Player')
        period=self.report()['periods']['all']
        daily={d['label']:d for d in period['daily_players']}
        self.assertEqual([daily[day]['total'] for day in sorted(daily)],[1,0,2,0])
        yesterday=daily[sorted(daily)[-2]]
        self.assertEqual((yesterday['native'],yesterday['browser'],yesterday['sessions'],yesterday['unidentified_sessions']),(1,1,3,1))
        self.assertTrue(all({*usage.RUNTIMES,'total','sessions','unidentified_sessions','install_ids'}<=set(d) for d in period['daily_players']))
        self.assertEqual(sum(m['sessions'] for m in period['monthly_players']),4)
        self.assertEqual(period['players']['total'],2)

    def test_install_keys_count_installations_and_absorb_their_names(self):
        self.db.execute('ALTER TABLE analytics_players ADD COLUMN install_key TEXT')
        def keyed(mid,name,key,rt='native',start=NOW-100):
            self.match(mid,rt=rt,start=start);self.player(mid,name=name)
            if key:self.db.execute('UPDATE analytics_players SET install_key=? WHERE match_id=?',(key,mid))
        keyed('rob1','Rob','a'*32);keyed('rob2','Rob','b'*32);keyed('rob3','Rob',None)
        keyed('dana-before','Dana',None,start=NOW-200);keyed('dana-after','Dana','c'*32)
        keyed('browser','Player','d'*32,rt='browser')
        players=self.report()['periods']['day']['players']
        # Rob's name belongs to two installations, so his unkeyed game stays a separate name; Dana's
        # earlier game joins her installation; a shared default name still counts by its install key.
        self.assertEqual((players['total'],players['install_ids'],players['browser'],players['unidentified_sessions']),(5,4,1,0))
        self.assertNotIn('a'*32,json.dumps(self.report()))

    def test_dunecity_campaign_games_players_and_missions(self):
        self.match('m1-win',rt='native',outcome='finished',map_name='DCITY_M01');self.player('m1-win',name='Alice',result='winner')
        self.match('m1-retry',rt='native',outcome='abandoned',map_name='dcity_m01.ini');self.player('m1-retry',name='Alice')
        self.match('m2-loss',rt='browser',outcome='finished',map_name='/maps/DCITY_M02.INI');self.player('m2-loss',name='Player',result='defeated')
        self.match('m10-open',rt='android',end=None,outcome='started',map_name='DCITY_M10');self.player('m10-open',name='Bob')
        self.match('old-day',rt='native',start=NOW-2*86400,outcome='finished',map_name='DCITY_M03');self.player('old-day',name='Carol',result='winner')
        self.match('other',map_name='SCENA001');self.player('other',name='Dave')
        self.match('not-a-mission',mode='single_custom',map_name='DCITY_M11');self.player('not-a-mission',name='Erin')
        report=self.report()
        day=report['periods']['day']['dunecity_campaign']
        self.assertEqual((day['games'],day['players']['total'],day['players']['unidentified_sessions'],day['won'],day['lost']),(4,2,1,1,1))
        missions={m['mission']:m for m in day['missions']}
        self.assertEqual(len(missions),10)
        self.assertEqual((missions[1]['name'],missions[1]['starts'],missions[1]['players'],missions[1]['won'],missions[1]['exited']),('Sihaya Basin',2,1,1,1))
        self.assertEqual((missions[2]['lost'],missions[2]['players'],missions[10]['missing_end'],missions[5]['starts']),(1,0,1,0))
        self.assertEqual(sum(d['total'] for d in day['daily']),4)
        every=report['periods']['all']['dunecity_campaign']
        self.assertEqual((every['games'],every['players']['total']),(5,3))
        # Zero days run from the first campaign game, not from when tracking began.
        self.assertEqual([d['total'] for d in every['daily']],[1,4,0])
        self.assertEqual([d['total'] for d in every['daily_players']],[1,2,0])
        self.assertNotIn('Alice',json.dumps(report))

    def test_no_campaign_games_and_map_list_is_bounded(self):
        for i in range(40):
            self.match('custom%d'%i,mode='single_custom',map_name='Map %02d'%i)
        period=self.report()['periods']['all']
        self.assertEqual((period['dunecity_campaign']['games'],period['dunecity_campaign']['since'],period['dunecity_campaign']['daily']),(0,None,[]))
        self.assertEqual(len(period['maps']),usage.MAP_LIMIT)

    def test_dunecity_campaign_is_its_own_mode(self):
        # The game reports DuneCity Campaign missions as game type "campaign".
        self.match('dc',rt='native',start=NOW-1000,end=NOW-100,outcome='finished',map_name='DCITY_M01',mod='dunecity')
        self.player('dc',name='Alice',result='winner');self.player('dc',house=2,team=2,controller='qbot',ai='qbot')
        self.match('legacy',rt='native',map_name='SCENA001');self.player('legacy',name='Bob')
        self.player('legacy',house=2,team=2,controller='ai',ai='campaign')
        p=self.report()['periods']['day']
        self.assertEqual({r['label']:r['total'] for r in p['modes']},{'campaign':1,'dunecity_campaign':1})
        self.assertEqual(sum(r['total'] for r in p['levels']),1)
        self.assertEqual(p['maps'],[])
        lengths={r['label']:r for r in p['lengths'] if r['runtime']=='native'}
        self.assertEqual((lengths['Campaign']['sessions'],lengths['DuneCity Campaign']['sessions'],lengths['Other modes']['sessions']),(1,1,0))
        self.assertEqual(lengths['DuneCity Campaign']['average_seconds'],900)
        opponents={r['label'] for r in p['bots']['ai'] if r['relation']=='opponent'}
        self.assertEqual(opponents,{'DuneCity Campaign · QuantBot','Campaign · Campaign AI'})

    def test_players_by_what_they_play(self):
        self.match('a-camp',rt='native',map_name='SCENA001');self.player('a-camp',name='Alice')
        self.match('a-camp2',rt='native',map_name='SCENA002');self.player('a-camp2',name='Alice')
        self.match('a-sk',rt='browser',mode='skirmish',map_name='Map',mod='dunecity');self.player('a-sk',name='Alice')
        self.match('b-dc',rt='native',map_name='DCITY_M02',mod='dunecity');self.player('b-dc',name='Bob')
        self.match('c-mp',rt='native',mode='multiplayer',map_name='Map');self.player('c-mp',name='Carol');self.player('c-mp',name='Dave')
        self.match('anon',rt='browser',mode='skirmish',map_name='Map');self.player('anon',name='Player')
        report=self.report();p=report['periods']['day']
        modes={r['label']:r for r in p['players_by']['modes']}
        self.assertEqual({k:v['total'] for k,v in modes.items()},{'campaign':1,'skirmish':1,'dunecity_campaign':1,'multiplayer':2})
        self.assertEqual((modes['campaign']['sessions'],modes['skirmish']['sessions'],modes['skirmish']['unidentified_sessions'],modes['skirmish']['browser']),(2,2,1,1))
        self.assertEqual([r['label'] for r in p['players_by']['modes']][0],'multiplayer')
        self.assertEqual({r['label']:r['total'] for r in p['players_by']['mods']},{'vanilla':3,'dunecity':2})
        levels={r['label']:r['total'] for r in p['players_by']['levels']}
        self.assertEqual(levels,{'1':1,'2':1})
        mix=p['player_mix']['modes']
        self.assertEqual(mix['players'],4)
        self.assertEqual(mix['combinations'],[{'labels':['multiplayer'],'players':2},
            {'labels':['dunecity_campaign'],'players':1},{'labels':['campaign','skirmish'],'players':1}])
        mods=p['player_mix']['mods']
        self.assertEqual({tuple(c['labels']):c['players'] for c in mods['combinations']},{('vanilla',):2,('dunecity',):1,('vanilla','dunecity'):1})
        for name in ['Alice','Bob','Carol','name:']:self.assertNotIn(name,json.dumps(report))

    def test_player_mix_is_bounded(self):
        modes=['campaign','skirmish','single_custom','multiplayer']
        for i in range(1,16):   # 15 distinct combinations of four modes, one player each
            for bit,mode in enumerate(modes):
                if i>>bit&1:
                    self.match('g%d-%s'%(i,mode),mode=mode,map_name='SCENA001' if mode=='campaign' else 'Map');self.player('g%d-%s'%(i,mode),name='P%d'%i)
        mix=self.report()['periods']['day']['player_mix']['modes']
        self.assertEqual((mix['players'],len(mix['combinations']),mix['other_players'],mix['other_combinations']),(15,usage.MIX_LIMIT,3,3))
        self.assertEqual(mix['combinations'][0]['labels'],['campaign'])

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

    def test_pinned_mod_copies_count_as_their_base_mod(self):
        import tempfile
        def manifest(root,base):
            digest=('%064x'%len(list((Path(root)/'manifests').glob('*'))))
            (Path(root)/'manifests'/digest).write_bytes(b'DUNEWORKSHOP1\nkind=mod\nname=00\nbase='+base.encode().hex().encode()+b'\nmod=\n')
            return 'ws-'+digest
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'manifests').mkdir()
            copies={'Dune City copy':manifest(root,'dunecity'),'Vanilla copy':manifest(root,'vanilla'),
                    'Tornie copy':manifest(root,'Tornie'),'player mod copy':manifest(root,'new')}
            names=['vanilla',*copies.values(),'ws-'+'f'*64,'ws-../../etc/passwd','Alternative Balance']
            for i,name in enumerate(names):self.match('m%d'%i,mod=name,mode='multiplayer');self.player('m%d'%i);self.player('m%d'%i)
            self.db.commit()
            mods={r['label']:r['total'] for r in usage.build_report(self.db,NOW,root)['periods']['all']['mods']}
            history=[g['mod'] for g in usage.build_history_page(self.db,1,NOW,root)['games']]
        # Unknown bases and copies missing from the store count as vanilla; malformed names are not looked up.
        self.assertEqual(mods,{'vanilla':4,'dunecity':1,'Tornie':1,'ws-../../etc/passwd':1,'Alternative Balance':1})
        self.assertEqual(sorted(history),sorted(['vanilla','dunecity','vanilla','Tornie','vanilla','vanilla','ws-../../etc/passwd','Alternative Balance']))

if __name__=='__main__':unittest.main()
