"""Public activity validation and storage through PHP/PDO and Python fallback."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('public_store', ROOT/'metaserver/public_activity_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)
PHP = shutil.which('php')


def event(kind='chat_message', number=1):
    e = dict(event_id=f'{number:032x}', kind=kind, occurred_at=1789190000, room_id='',
             player_name='Álice', message='', channel_id='', mod_name='', mode='',
             game_version='', participant_id=0, role='', start_id='', players=[])
    if kind == 'chat_message':
        e.update(message="Hello Bob — let's play!", channel_id='c'*64)
    else:
        e.update(room_id='room-'+'b'*32, mod_name='dunecity', mode='custom', game_version='1.0.725')
        if kind == 'public_game_started':
            e.update(start_id='d'*32, players=[
                dict(id=1, name='Álice', role='host', runtime='native'),
                dict(id=2, name='Bob', role='client', runtime='browser')])
        else:
            e.update(participant_id=1 if kind=='public_game_created' else 2,
                     role='host' if kind=='public_game_created' else 'client')
    return e


class PublicActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def php(self, events, args=(), normalize=False):
        if not PHP: self.skipTest('PHP CLI unavailable')
        code = "define('DATA_DIR',$argv[1]);require $argv[2];"
        code += "$rows=json_decode(stream_get_contents(STDIN),true);$out=[];foreach($rows as $e){"
        code += "$out[]=publicActivityNormalize($e)!==null;" if normalize else "$out[]=publicActivityRecord($e);"
        code += "}echo json_encode($out);"
        run = subprocess.run([PHP, *args, '-r', code, self.temp.name,
                              str(ROOT/'metaserver/public_activity.php')],
                             input=json.dumps(events), text=True, capture_output=True, check=True)
        return json.loads(run.stdout)

    def invalid(self):
        for changes in [dict(session='secret'), dict(kind='private_game'), dict(event_id='x'),
                        dict(occurred_at=True), dict(player_name=''), dict(player_name='x'*65),
                        dict(message='x'*121), dict(message='fake\nline'), dict(channel_id='x'),
                        dict(players=[dict(id=1)]), dict(participant_id=True), dict(room_id='secret-code')]:
            yield {**event(), **changes}
        for changes in [dict(players=[]), dict(start_id=''), dict(role='host')]:
            yield {**event('public_game_started'), **changes}
        for field, value in [('id',1), ('name','x\u202e'), ('runtime','anything'), ('role','host')]:
            e=event('public_game_started'); e['players'][1][field]=value; yield e
        e=event('public_game_started'); e['players'][0]['name']='Forged host'; yield e

    def test_validation_parity(self):
        valid=[event(k,i+1) for i,k in enumerate(store.KINDS)]
        invalid=list(self.invalid())
        self.assertEqual([True]*len(valid)+[False]*len(invalid), self.php(valid+invalid, normalize=True))
        for e in valid: self.assertIsNotNone(store.normalize(e))
        for e in invalid:
            with self.subTest(e=e), self.assertRaises(ValueError): store.normalize(e)

    def verify_storage(self, args=()):
        dbpath=Path(self.temp.name)/'games.sqlite'
        db=sqlite3.connect(dbpath)
        db.executescript((ROOT/'scripts/tests/fixtures/relay_analytics_schema1.sql').read_text())
        db.execute("INSERT INTO analytics_relay_events (event_id,room_id,kind,occurred_at,received_at,participant_id,client_runtime,game_version,reason) VALUES (?,?,?,?,?,?,?,?,?)",
                   ('event-'+'0'*32,'room-'+'0'*32,'joined',1789180000,1789180001,1,'browser','1.0.655',''))
        db.commit()
        legacy=db.execute('SELECT * FROM analytics_relay_events').fetchall()
        rows=[event(k,i+1) for i,k in enumerate(store.KINDS)]
        self.assertEqual([True]*4,self.php(rows,args))
        retry=copy.deepcopy(rows)
        retry[-1]['players']=[dict(reversed(list(p.items()))) for p in retry[-1]['players']]
        self.assertEqual([True]*4,self.php(retry,args))
        self.assertEqual([False],self.php([{**rows[0],'message':'Conflict'}],args))
        self.assertEqual([False],self.php([{**rows[0],'session':'secret'}],args))
        self.assertEqual(4,db.execute('SELECT count(*) FROM analytics_public_activity').fetchone()[0])
        self.assertEqual(rows[0]['message'],db.execute("SELECT message FROM analytics_public_activity WHERE kind='chat_message'").fetchone()[0])
        roster=json.loads(db.execute("SELECT details_json FROM analytics_public_activity WHERE kind='public_game_started'").fetchone()[0])['players']
        self.assertEqual(rows[-1]['players'],roster)
        self.assertEqual(legacy,db.execute('SELECT * FROM analytics_relay_events').fetchall())
        db.close()

    def test_php_storage_preserves_old_data_and_deduplicates(self): self.verify_storage()
    def test_php_without_sqlite_uses_python(self): self.verify_storage(('-n',))

    def test_direct_python_store(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        e=event('public_game_started')
        store.record(db,e); store.record(db,e)
        with self.assertRaises(ValueError): store.record(db,{**e,'game_version':'1.0.1'})
        self.assertEqual(1,db.execute('SELECT count(*) FROM analytics_public_activity').fetchone()[0])


if __name__ == '__main__': unittest.main()
