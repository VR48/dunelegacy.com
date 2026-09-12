import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('store', Path(__file__).resolve().parents[2] / 'metaserver/analytics_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def event(number=1, participant=1, runtime='browser', kind='joined'):
    return dict(schema_version=1, event_id=f'event-{number:032d}', room_id='room-' + 'a' * 32,
                kind=kind, occurred_at=1789190000 + number, participant_id=participant,
                client_runtime=runtime, game_version='1.0.655', reason='')


class RelayAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = store.open_database(str(Path(self.temp.name) / 'games.sqlite'))
        store.record(self.db, dict(phase='start', match_id='legacy-match', payload={}))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_mixed_room_events_are_idempotent_and_keep_legacy_match(self):
        for row in (event(), event(), event(2, 2, 'native'), event(3, kind='left')):
            store.relay_record(self.db, {'event': row})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_relay_events').fetchone()[0], 3)
        self.assertEqual(self.db.execute('SELECT participant_id,client_runtime,transport,runtime_source FROM analytics_relay_participants ORDER BY participant_id').fetchall(),
                         [(1, 'browser', 'wss', 'client_reported'), (2, 'native', 'wss', 'client_reported')])
        self.assertEqual(self.db.execute('SELECT match_id,client_runtime FROM analytics_matches').fetchall(), [('legacy-match', 'unknown')])

    def test_out_of_order_departure_and_retried_event_cannot_rewrite_history(self):
        leave = event(3, kind='left')
        store.relay_record(self.db, {'event': leave})
        store.relay_record(self.db, {'event': event()})
        conflict = event()
        conflict['client_runtime'] = 'native'
        store.relay_record(self.db, {'event': conflict})
        self.assertEqual(self.db.execute('SELECT client_runtime,joined_at,left_at FROM analytics_relay_participants').fetchone(),
                         ('browser', 1789190001, 1789190003))

    def test_rejects_secrets_unbounded_invalid_and_forged_transport_fields(self):
        invalid = [dict(ticket='secret'), dict(transport='udp'), dict(schema_version=True),
                   dict(event_id='short'), dict(room_id="x' OR 1=1 --"), dict(kind='anything'),
                   dict(participant_id=True), dict(participant_id=2**32), dict(occurred_at=1.5),
                   dict(client_runtime={'runtime':'browser'}), dict(reason='x' * 49),
                   dict(reason='chat\nbody'), dict(kind='started'), dict(game_version='x' * 65)]
        for changes in invalid:
            with self.subTest(changes=changes):
                row = copy.deepcopy(event()); row.update(changes)
                with self.assertRaises(ValueError):
                    store.relay_record(self.db, {'event': row})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_matches').fetchone()[0], 1)

if __name__ == '__main__':
    unittest.main()

# Exercise the real HTTP boundary; all data, credentials and database are disposable fixtures.
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

@unittest.skipUnless(shutil.which('php'), 'PHP CLI is required for the HTTP endpoint test')
class RelayEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.key = 'local-test-key-' * 4
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); cls.port = sock.getsockname()[1]
        root = Path(__file__).resolve().parents[2] / 'metaserver'
        cls.process = subprocess.Popen(['php', '-S', f'127.0.0.1:{cls.port}', '-t', str(root)],
            env={**os.environ, 'DATA_DIR':cls.temp.name, 'DUNE_RELAY_ANALYTICS_KEY':cls.key},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.url = f'http://127.0.0.1:{cls.port}/relay-events.php'
        for _ in range(40):
            try:
                urllib.request.urlopen(cls.url, timeout=0.1)
            except urllib.error.HTTPError:
                break
            except urllib.error.URLError:
                time.sleep(0.05)
        else:
            cls.process.terminate(); cls.process.wait(); cls.temp.cleanup()
            raise RuntimeError('PHP test server did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate(); cls.process.wait(timeout=5); cls.temp.cleanup()

    def post(self, raw, signed=True, timestamp=None):
        timestamp = str(int(time.time())) if timestamp is None else timestamp
        signature = hmac.new(self.key.encode(), timestamp.encode()+b'\n'+raw, hashlib.sha256).hexdigest()
        request = urllib.request.Request(self.url, raw, {
            'Content-Type':'application/json', 'X-Dune-Relay-Timestamp':timestamp,
            'X-Dune-Relay-Signature':signature if signed else '0'*64})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status
        except urllib.error.HTTPError as response:
            return response.code

    def test_http_authentication_bounds_idempotency_and_legacy_isolation(self):
        raw = json.dumps(event()).encode()
        self.assertEqual(self.post(raw, signed=False), 401)
        self.assertFalse((Path(self.temp.name)/'games.sqlite').exists())
        self.assertEqual(self.post(raw, timestamp='1000000000'), 401)
        self.assertEqual(self.post(b'x'*4097), 413)
        self.assertEqual(self.post(json.dumps({**event(),'ticket':'do not store'}).encode()), 400)
        self.assertEqual(self.post(raw), 200)
        self.assertEqual(self.post(raw), 200)
        import sqlite3
        with sqlite3.connect(Path(self.temp.name)/'games.sqlite') as connection:
            self.assertEqual(connection.execute('select count(*) from analytics_relay_events').fetchone()[0],1)
            self.assertEqual(connection.execute('select count(*) from analytics_matches').fetchone()[0],0)
