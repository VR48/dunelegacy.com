"""usage.php against PHP's built-in server and a temporary DATA_DIR; never production data."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('analytics_store', ROOT / 'metaserver/analytics_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


@unittest.skipUnless(shutil.which('php') and Path('/usr/bin/python3').exists(), 'needs php and /usr/bin/python3')
class UsageEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.cache = self.data / 'usage-public-v1.json'
        database = str(self.data / 'games.sqlite')
        connection = store.open_database(database)
        store.record(connection, {'phase': 'start', 'match_id': 'endpoint-test-0001', 'database': database,
                                  'payload': {'schema_version': 3, 'game_type': 'campaign', 'client_runtime': 'native',
                                              'map': {'name': 'DCITY_M01'},
                                              'players': [{'slot': 0, 'player_name': 'Tester', 'controller': 'human'}]}})
        connection.close()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.server = subprocess.Popen(['php', '-S', f'127.0.0.1:{self.port}', '-t', str(ROOT / 'metaserver')],
                                       env={**os.environ, 'DATA_DIR': str(self.data)},
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.05)

    def tearDown(self):
        self.server.terminate()
        self.server.wait(timeout=10)
        self.temp.cleanup()

    def get(self, gzip_ok=False):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/usage.php',
                                         headers={'Accept-Encoding': 'gzip'} if gzip_ok else {})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            self.assertEqual(int(response.headers['Content-Length']), len(body))
            return response.headers, body

    def test_first_request_builds_the_report_and_a_gzip_copy(self):
        headers, body = self.get()
        report = json.loads(body)
        self.assertEqual(report['schema'], 1)
        self.assertEqual(report['periods']['all']['dunecity_campaign']['games'], 1)
        self.assertIsNone(headers['Content-Encoding'])
        headers, packed = self.get(gzip_ok=True)
        self.assertEqual(headers['Content-Encoding'], 'gzip')
        self.assertEqual(gzip.decompress(packed), self.cache.read_bytes())
        self.assertLess(len(packed), len(body))

    def test_stale_report_is_answered_at_once_and_rebuilt_afterwards(self):
        old = b'{"schema":1,"generated":"stale","periods":{}}'
        self.cache.write_bytes(old)
        (self.data / 'usage-public-v1.json.gz').write_bytes(gzip.compress(old))
        stale = time.time() - 600
        for path in (self.cache, self.data / 'usage-public-v1.json.gz'):
            os.utime(path, (stale, stale))
        headers, body = self.get(gzip_ok=True)
        self.assertEqual(gzip.decompress(body), old)
        for _ in range(200):
            if self.cache.read_bytes() != old:
                break
            time.sleep(0.05)
        rebuilt = json.loads(self.cache.read_bytes())
        self.assertNotEqual(rebuilt['generated'], 'stale')
        self.assertEqual(gzip.decompress((self.data / 'usage-public-v1.json.gz').read_bytes()), self.cache.read_bytes())
        _, fresh = self.get()
        self.assertEqual(json.loads(fresh)['generated'], rebuilt['generated'])

    def test_gzip_copy_older_than_the_report_is_not_served(self):
        self.get()
        older = self.cache.stat().st_mtime - 5
        os.utime(self.data / 'usage-public-v1.json.gz', (older, older))
        headers, body = self.get(gzip_ok=True)
        self.assertIsNone(headers['Content-Encoding'])
        self.assertEqual(body, self.cache.read_bytes())


if __name__ == '__main__':
    unittest.main()
