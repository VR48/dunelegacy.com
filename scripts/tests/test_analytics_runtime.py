import importlib.util
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


if __name__ == '__main__':
    unittest.main()
