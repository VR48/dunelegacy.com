import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('analytics_store', Path(__file__).resolve().parents[2] / 'metaserver/analytics_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class RuntimeAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'games.sqlite')
        self.db = store.open_database(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def record(self, phase='start', runtime=None, match='test-browser-0001'):
        payload = {'schema_version': 3, 'game_type': 'skirmish', 'players': []}
        if runtime is not None:
            payload['client_runtime'] = runtime
        store.record(self.db, {'phase': phase, 'match_id': match, 'payload': payload})
        return self.db.execute('SELECT client_runtime FROM analytics_matches WHERE match_id=?', (match,)).fetchone()[0]

    def test_browser_native_and_old_clients(self):
        self.assertEqual(self.record(runtime='browser'), 'browser')
        self.assertEqual(self.record(runtime='native', match='test-native-0001'), 'native')
        self.assertEqual(self.record(runtime='android', match='test-android-0001'), 'android')
        self.assertEqual(self.record(runtime='ios', match='test-ios-0001'), 'ios')
        self.assertEqual(self.record(match='test-legacy-0001'), 'unknown')
        self.assertEqual(self.record(runtime='forged', match='test-invalid-0001'), 'unknown')
        self.assertEqual(self.record(runtime={'name': 'browser'}, match='test-invalid-0002'), 'unknown')

    def test_end_only_duplicate_and_older_events_keep_known_runtime(self):
        self.assertEqual(self.record('end', 'browser'), 'browser')
        self.assertEqual(self.record('start'), 'browser')
        self.assertEqual(self.record('end', 'unsupported'), 'browser')
        self.assertEqual(self.record('end', 'browser'), 'browser')
        self.assertEqual(self.db.execute('SELECT COUNT(*), outcome FROM analytics_matches').fetchone(), (1, 'finished'))

    def test_migrates_old_database_without_rewriting_rows(self):
        self.db.close()
        Path(self.path).unlink()
        old = sqlite3.connect(self.path)
        old.executescript(store.SCHEMA.replace("    client_runtime TEXT NOT NULL DEFAULT 'unknown',\n", ''))
        old.execute("INSERT INTO analytics_matches(match_id,source,updated_at,start_json) VALUES ('historic-game','legacy_gamestart',1,'{}')")
        old.commit()
        old.close()
        self.db = store.open_database(self.path)
        self.assertEqual(self.db.execute('SELECT client_runtime,source,updated_at,start_json FROM analytics_matches').fetchone(), ('unknown','legacy_gamestart',1,'{}'))
        self.assertEqual(self.record(runtime='browser'), 'browser')
        self.db.close()
        self.db = store.open_database(self.path)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_matches').fetchone()[0], 2)

    def install_payload(self, raw, name='Host'):
        return {'schema_version': 3, 'game_type': 'multiplayer', 'install_id': raw, 'players': [
            {'slot': 0, 'player_name': name, 'controller': 'human', 'install_id': raw},
            {'slot': 1, 'player_name': 'Bot', 'controller': 'qbot', 'install_id': 'f' * 32},
            {'slot': 2, 'player_name': 'Guest', 'controller': 'human', 'install_id': 'NOT-HEX'},
            {'slot': 3, 'player_name': 'Other', 'controller': 'human'}]}

    def test_install_ids_are_stored_only_as_keyed_hashes(self):
        raw = '0123456789abcdef0123456789abcdef'
        for phase in ('start', 'end'):
            store.record(self.db, {'phase': phase, 'match_id': 'install-test-0001',
                                   'payload': self.install_payload(raw), 'database': self.path})
        secret = Path(self.temp.name) / 'analytics-install-key'
        expected = hmac.new(bytes.fromhex(secret.read_text().strip()), raw.encode(), hashlib.sha256).hexdigest()[:32]
        self.assertEqual(self.db.execute('SELECT slot, install_key FROM analytics_players ORDER BY slot').fetchall(),
                         [(0, expected), (1, None), (2, None), (3, None)])
        self.assertEqual(secret.stat().st_mode & 0o777, 0o640)
        stored = '\n'.join(self.db.iterdump())
        for private in (raw, 'f' * 32, 'NOT-HEX', 'install_id'):
            self.assertNotIn(private, stored)
        start = json.loads(self.db.execute('SELECT start_json FROM analytics_matches').fetchone()[0])
        self.assertEqual(start['players'][0], {'slot': 0, 'player_name': 'Host', 'controller': 'human'})
        # One installation keeps one key across games; the key file is created once and kept.
        before = secret.read_text()
        store.record(self.db, {'phase': 'start', 'match_id': 'install-test-0002',
                               'payload': self.install_payload(raw, 'Renamed'), 'database': self.path})
        self.assertEqual(secret.read_text(), before)
        self.assertEqual(self.db.execute("SELECT COUNT(DISTINCT install_key) FROM analytics_players"
                                         " WHERE install_key IS NOT NULL").fetchone()[0], 1)

    def test_unusable_key_file_drops_the_install_id(self):
        (Path(self.temp.name) / 'analytics-install-key').write_text('not a key\n')
        raw = 'a' * 32
        store.record(self.db, {'phase': 'start', 'match_id': 'install-test-0003',
                               'payload': self.install_payload(raw), 'database': self.path})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_players WHERE install_key IS NOT NULL').fetchone()[0], 0)
        self.assertNotIn(raw, '\n'.join(self.db.iterdump()))

    def test_install_key_column_is_added_to_older_databases(self):
        self.db.close()
        Path(self.path).unlink()
        old = sqlite3.connect(self.path)
        old.executescript(store.SCHEMA)
        old.close()
        self.db = store.open_database(self.path)
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(analytics_players)')}
        self.assertIn('install_key', columns)


if __name__ == '__main__':
    unittest.main()
