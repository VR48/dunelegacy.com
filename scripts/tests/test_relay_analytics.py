import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
FIXTURE_SCHEMA1 = Path(__file__).resolve().parent / 'fixtures/relay_analytics_schema1.sql'

spec = importlib.util.spec_from_file_location('store', REPO / 'metaserver/analytics_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def event(number=1, participant=1, runtime='browser', kind='joined', schema=1, transport=None):
    row = dict(schema_version=schema, event_id=f'event-{number:032d}', room_id='room-' + 'a' * 32,
               kind=kind, occurred_at=1789190000 + number, participant_id=participant,
               client_runtime=runtime, game_version='1.0.655', reason='')
    if transport is not None:
        row['transport'] = transport
    return row


def poll_event(number=1, participant=1, runtime='browser', kind='joined'):
    """A schema-2 event from a relay whose server-observed ingress is HTTPS polling."""
    return event(number, participant, runtime, kind, schema=2, transport='https-poll')


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
        self.assertEqual(self.db.execute('SELECT participant_id,client_runtime,transport,runtime_source,transport_source FROM analytics_relay_participants ORDER BY participant_id').fetchall(),
                         [(1, 'browser', 'wss', 'client_reported', 'server_observed'),
                          (2, 'native', 'wss', 'client_reported', 'server_observed')])
        self.assertEqual(self.db.execute('SELECT match_id,client_runtime FROM analytics_matches').fetchall(), [('legacy-match', 'unknown')])

    def test_out_of_order_departure_and_retried_event_cannot_rewrite_history(self):
        leave = event(3, kind='left')
        store.relay_record(self.db, {'event': leave})
        store.relay_record(self.db, {'event': event()})
        conflict = event()
        conflict['client_runtime'] = 'native'
        with self.assertRaises(store.RelayEventConflict):
            store.relay_record(self.db, {'event': conflict})
        self.assertEqual(self.db.execute('SELECT client_runtime,joined_at,left_at FROM analytics_relay_participants').fetchone(),
                         ('browser', 1789190001, 1789190003))

    def test_sql_constraints_reject_invalid_rows(self):
        import sqlite3
        store.relay_record(self.db, {'event': event()})
        for field, value in [('event_id','x'),('occurred_at',-1),('participant_id',0),('reason','private body'),('room_id','short')]:
            row=event(2); row[field]=value
            with self.subTest(field=field), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute("""INSERT INTO analytics_relay_events
                    (event_id,room_id,kind,occurred_at,received_at,participant_id,client_runtime,game_version,reason)
                    VALUES (?,?,?,?,?,?,?,?,?)""", tuple(row[key] for key in ('event_id','room_id','kind','occurred_at'))+
                    (1789190000,)+tuple(row[key] for key in ('participant_id','client_runtime','game_version','reason')))
        # The column allowlist is a constraint too: only server-observed transports are storable.
        for transport in ('ws', 'udp', 'WSS', 'https', ''):
            with self.subTest(transport=transport), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute("""INSERT INTO analytics_relay_events
                    (event_id,room_id,kind,occurred_at,received_at,participant_id,client_runtime,game_version,reason,transport)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    ('event-' + 'z' * 32, 'room-' + 'a' * 32, 'joined', 1789190000, 1789190000,
                     1, 'browser', '1.0.655', '', transport))

    def test_rejects_secrets_unbounded_invalid_and_forged_transport_fields(self):
        invalid = [dict(ticket='secret'), dict(transport='udp'), dict(schema_version=True),
                   dict(event_id='short'), dict(room_id="x' OR 1=1 --"), dict(kind='anything'),
                   dict(participant_id=True), dict(participant_id=2**32), dict(occurred_at=1.5),
                   dict(client_runtime={'runtime':'browser'}), dict(reason='x' * 49),
                   dict(reason='chat\nbody'), dict(kind='started'), dict(game_version='x' * 65),
                   # A schema-1 record has no transport field and can only ever mean wss.
                   dict(transport='https-poll'), dict(schema_version=3, transport='wss')]
        for changes in invalid:
            with self.subTest(changes=changes):
                row = copy.deepcopy(event()); row.update(changes)
                with self.assertRaises(ValueError):
                    store.relay_record(self.db, {'event': row})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_matches').fetchone()[0], 1)

    def test_schema_two_records_the_server_observed_transport(self):
        store.relay_record(self.db, {'event': poll_event()})
        store.relay_record(self.db, {'event': poll_event(2, 2, 'native')})
        store.relay_record(self.db, {'event': event(3, 3, 'native')})
        # Schema 2 without a transport, and schema 2 with anything but an allowlisted one.
        for changes in (dict(transport=None), dict(transport='wss '), dict(transport='ws'),
                        dict(transport='udp'), dict(transport=True), dict(transport=['wss']),
                        dict(transport='HTTPS-POLL')):
            row = poll_event(4, 4)
            if changes['transport'] is None:
                row.pop('transport')
            else:
                row.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store.relay_record(self.db, {'event': row})
        self.assertEqual(self.db.execute('SELECT event_id,client_runtime,transport FROM analytics_relay_events ORDER BY event_id').fetchall(),
                         [('event-' + '0' * 31 + '1', 'browser', 'https-poll'),
                          ('event-' + '0' * 31 + '2', 'native', 'https-poll'),
                          ('event-' + '0' * 31 + '3', 'native', 'wss')])
        # Browser/native classification stays orthogonal to, and queryable beside, the transport.
        self.assertEqual(self.db.execute('SELECT transport,client_runtime FROM analytics_relay_participants ORDER BY participant_id').fetchall(),
                         [('https-poll', 'browser'), ('https-poll', 'native'), ('wss', 'native')])

    def test_an_event_id_cannot_be_downgraded_to_the_legacy_transport(self):
        store.relay_record(self.db, {'event': poll_event()})
        store.relay_record(self.db, {'event': poll_event()})  # an ordinary delivery retry
        # The same id offered again as a schema-1 (wss) event, and as an explicit wss event.
        for retry in (event(), dict(poll_event(), transport='wss')):
            with self.subTest(schema=retry['schema_version']), self.assertRaises(store.RelayEventConflict):
                store.relay_record(self.db, {'event': retry})
        self.assertEqual(self.db.execute('SELECT transport FROM analytics_relay_events').fetchall(),
                         [('https-poll',)])


class RelayMigrationTests(unittest.TestCase):
    """The schema-1 table that is actually deployed, migrated in place by the receiver."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'games.sqlite')
        self.db = store.open_database(self.path)
        self.db.executescript(FIXTURE_SCHEMA1.read_text())
        store.record(self.db, dict(phase='start', match_id='legacy-match', payload={}))
        self.legacy = ('event-' + '9' * 32, 'room-' + '9' * 32, 'joined', 1789180000, 1789180001,
                       1, 'browser', '1.0.655', '')
        self.db.execute("""INSERT INTO analytics_relay_events
            (event_id,room_id,kind,occurred_at,received_at,participant_id,client_runtime,game_version,reason)
            VALUES (?,?,?,?,?,?,?,?,?)""", self.legacy)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def table_sql(self, name='analytics_relay_events'):
        row = self.db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        return None if row is None else row[0]

    def test_fixture_really_is_the_old_schema(self):
        self.assertIn("CHECK(transport = 'wss')", self.table_sql())
        self.assertIn("'wss' AS transport", self.table_sql('analytics_relay_participants'))
        with self.assertRaises(Exception):
            self.db.execute("UPDATE analytics_relay_events SET transport='https-poll'")

    def test_migration_preserves_rows_indexes_and_legacy_tables(self):
        store.relay_record(self.db, {'event': poll_event()})
        self.assertIn("'https-poll'", self.table_sql())
        self.assertIn('server_observed', self.table_sql('analytics_relay_participants'))
        # Every schema-1 row survives byte for byte, and keeps meaning wss.
        self.assertEqual(self.db.execute("""SELECT event_id,room_id,kind,occurred_at,received_at,
            participant_id,client_runtime,game_version,reason FROM analytics_relay_events
            WHERE event_id=?""", (self.legacy[0],)).fetchone(), self.legacy)
        self.assertEqual(self.db.execute('SELECT transport,source FROM analytics_relay_events WHERE event_id=?',
                                         (self.legacy[0],)).fetchone(), ('wss', 'relay_service_v1'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_relay_events').fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='analytics_relay_room_idx'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%schema1%'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM analytics_matches WHERE match_id='legacy-match'").fetchone()[0], 1)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_migrated_database_keeps_schema_one_behaviour(self):
        store.relay_record(self.db, {'event': poll_event()})
        # A relay that has not been updated yet still delivers schema-1 events afterwards.
        store.relay_record(self.db, {'event': event(2)})
        store.relay_record(self.db, {'event': event(2)})
        conflict = event(2)
        conflict['client_runtime'] = 'native'
        with self.assertRaises(store.RelayEventConflict):
            store.relay_record(self.db, {'event': conflict})
        self.assertEqual(self.db.execute('SELECT transport,COUNT(*) FROM analytics_relay_events GROUP BY transport ORDER BY transport').fetchall(),
                         [('https-poll', 1), ('wss', 2)])
        # The old constraints are still constraints after the rebuild.
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""INSERT INTO analytics_relay_events
                (event_id,room_id,kind,occurred_at,received_at,participant_id,client_runtime,game_version,reason)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                ('event-' + 'y' * 32, 'room-' + 'a' * 32, 'created', 1789190000, 1789190000, 5,
                 'browser', '1.0.655', ''))

    def test_migration_runs_once_and_is_a_no_op_afterwards(self):
        store.relay_record(self.db, {'event': poll_event()})
        migrated = self.table_sql()
        store.relay_migrate(self.db)
        store.relay_record(self.db, {'event': poll_event(2, 2, 'native')})
        self.assertEqual(self.table_sql(), migrated)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_relay_events').fetchone()[0], 3)

    def test_a_failing_migration_leaves_the_schema_one_database_untouched(self):
        # A stale rebuild copy from a previous crash makes the rename fail: the receiver must
        # report storage trouble, not half-migrate or drop the rows it already has.
        self.db.execute('CREATE TABLE analytics_relay_events_schema1 (event_id TEXT)')
        self.db.commit()
        with self.assertRaises(Exception):
            store.relay_record(self.db, {'event': poll_event()})
        self.assertIn("CHECK(transport = 'wss')", self.table_sql())
        self.assertIn("'wss' AS transport", self.table_sql('analytics_relay_participants'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analytics_relay_events').fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='analytics_relay_room_idx'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM analytics_matches WHERE match_id='legacy-match'").fetchone()[0], 1)


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

PHP = shutil.which('php')


def php_has_pdo_sqlite(*php_args):
    probe = 'echo class_exists("PDO") && in_array("sqlite", PDO::getAvailableDrivers(), true) ? "yes" : "no";'
    result = subprocess.run([PHP, *php_args, '-r', probe], capture_output=True, text=True)
    return result.stdout.strip() == 'yes'


@unittest.skipUnless(PHP, 'PHP CLI is required for the HTTP endpoint test')
class RelayEndpointBase(unittest.TestCase):
    """One disposable PHP server per class. Subclasses vary how the key reaches it."""

    PHP_ARGS = ()
    KEY_IN_FILE = False
    KEY_MODE = 0o600

    @classmethod
    def setUpClass(cls):
        if cls is RelayEndpointBase:
            raise unittest.SkipTest('base fixture')
        cls.temp = tempfile.TemporaryDirectory()
        cls.key = 'local-test-key-' * 4
        env = {**os.environ, 'DATA_DIR': cls.temp.name}
        env.pop('DUNE_RELAY_ANALYTICS_KEY', None)
        env.pop('DUNE_RELAY_ANALYTICS_KEY_FILE', None)
        env.pop('RELAY_ANALYTICS_KEY_FILE', None)
        if cls.KEY_IN_FILE:
            key_path = Path(cls.temp.name) / 'analytics.key'
            key_path.write_text(cls.key + '\n')
            key_path.chmod(cls.KEY_MODE)
            env['DUNE_RELAY_ANALYTICS_KEY_FILE'] = str(key_path)
        else:
            env['DUNE_RELAY_ANALYTICS_KEY'] = cls.key
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); cls.port = sock.getsockname()[1]
        root = REPO / 'metaserver'
        cls.process = subprocess.Popen([PHP, *cls.PHP_ARGS, '-S', f'127.0.0.1:{cls.port}', '-t', str(root)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    @property
    def database(self):
        return Path(self.temp.name) / 'games.sqlite'

    def post(self, raw, signed=True, timestamp=None):
        timestamp = str(int(time.time())) if timestamp is None else timestamp
        signature = hmac.new(self.key.encode(), timestamp.encode()+b'\n'+raw, hashlib.sha256).hexdigest()
        request = urllib.request.Request(self.url, raw, {
            'Content-Type':'application/json', 'X-Dune-Relay-Timestamp':timestamp,
            'X-Dune-Relay-Signature':signature if signed else '0'*64})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as response:
            return response.code

    def rows(self, statement, *parameters):
        import sqlite3
        with sqlite3.connect(self.database) as connection:
            return connection.execute(statement, parameters).fetchall()


class RelayEndpointTests(RelayEndpointBase):
    def test_http_authentication_bounds_idempotency_and_legacy_isolation(self):
        raw = json.dumps(event()).encode()
        self.assertEqual(self.post(raw, signed=False), 401)
        self.assertEqual(self.post(raw, timestamp='1000000000'), 401)
        self.assertEqual(self.post(b'x'*4097), 413)
        self.assertEqual(self.post(json.dumps({**event(),'ticket':'do not store'}).encode()), 400)
        # Nothing refused above reached storage; an unconfigured endpoint does not even open the
        # database (RelayEndpointUnconfiguredTests).
        self.assertEqual(self.rows('select count(*) from analytics_relay_events where event_id=?',
                                   event()['event_id']), [(0,)])
        self.assertEqual(self.post(raw), 200)
        self.assertEqual(self.post(raw), 200)
        conflict = {**event(), 'client_runtime':'native'}
        self.assertEqual(self.post(json.dumps(conflict).encode()), 409)
        self.assertEqual(self.rows('select count(*) from analytics_relay_events where event_id=?',
                                   event()['event_id']), [(1,)])
        # Relay lifecycle events never create or touch a match row.
        self.assertEqual(self.rows('select count(*) from analytics_matches')[0][0], 0)

    def test_schema_two_transport_is_stored_and_cannot_be_forged_or_downgraded(self):
        self.assertEqual(self.post(json.dumps(poll_event(20, 2)).encode()), 200)
        self.assertEqual(self.post(json.dumps(poll_event(20, 2)).encode()), 200)
        # A schema-1 retry of that id would relabel an HTTPS-polling room as wss.
        self.assertEqual(self.post(json.dumps(event(20, 2)).encode()), 409)
        self.assertEqual(self.post(json.dumps({**poll_event(20, 2), 'transport': 'wss'}).encode()), 409)
        # Unknown, malformed and missing transports are refused before any storage happens.
        for body in ({**poll_event(21, 3), 'transport': 'udp'},
                     {**poll_event(21, 3), 'transport': 'ws'},
                     {**poll_event(21, 3), 'transport': ''},
                     {**poll_event(21, 3), 'transport': True},
                     {k: v for k, v in poll_event(21, 3).items() if k != 'transport'},
                     {**event(21, 3), 'transport': 'https-poll'},
                     {**poll_event(21, 3), 'schema_version': 3}):
            with self.subTest(body=body):
                self.assertEqual(self.post(json.dumps(body).encode()), 400)
        self.assertEqual(self.rows('select transport,client_runtime from analytics_relay_events where participant_id=2'),
                         [('https-poll', 'browser')])
        self.assertEqual(self.rows("select count(*) from analytics_relay_events where transport not in ('wss','https-poll')")[0][0], 0)

    def test_both_schemas_and_both_runtimes_coexist_in_one_database(self):
        self.assertEqual(self.post(json.dumps(event(30, 4, 'native')).encode()), 200)
        self.assertEqual(self.post(json.dumps(poll_event(31, 5, 'browser')).encode()), 200)
        self.assertEqual(self.rows('select transport,client_runtime,transport_source,runtime_source'
                                   ' from analytics_relay_participants where participant_id in (4,5)'
                                   ' order by participant_id'),
                         [('wss', 'native', 'server_observed', 'client_reported'),
                          ('https-poll', 'browser', 'server_observed', 'client_reported')])


@unittest.skipUnless(PHP, 'PHP CLI is required for the HTTP endpoint test')
class RelayEndpointKeyFileTests(RelayEndpointBase):
    """The deployment shape Apache will use: no key in the environment, a key file instead."""

    KEY_IN_FILE = True

    def test_a_key_file_authenticates_the_same_signed_request(self):
        self.assertEqual(self.post(json.dumps(poll_event(40, 6)).encode()), 200)
        self.assertEqual(self.post(json.dumps(poll_event(40, 6)).encode(), signed=False), 401)
        self.assertEqual(self.rows('select transport from analytics_relay_events'), [('https-poll',)])


@unittest.skipUnless(PHP, 'PHP CLI is required for the HTTP endpoint test')
class RelayEndpointUnconfiguredTests(RelayEndpointBase):
    """No usable key anywhere: the endpoint fails closed instead of storing anything."""

    KEY_IN_FILE = True
    KEY_MODE = 0o644  # a key every local account can read is not a key

    def test_a_world_readable_key_file_disables_the_endpoint(self):
        self.assertEqual(self.post(json.dumps(poll_event(50, 7)).encode()), 503)
        self.assertFalse(self.database.exists())


@unittest.skipUnless(PHP and not php_has_pdo_sqlite('-n'),
                     'a PHP build without pdo_sqlite is required for the fallback test')
class RelayEndpointPythonFallbackTests(RelayEndpointBase):
    """The production backend today: PHP without pdo_sqlite, storing through analytics_store.py."""

    PHP_ARGS = ('-n',)
    KEY_IN_FILE = True

    def test_signed_https_poll_events_reach_sqlite_through_the_python_helper(self):
        self.assertEqual(self.post(json.dumps(poll_event(60, 8)).encode()), 200)
        self.assertEqual(self.post(json.dumps(poll_event(60, 8)).encode()), 200)
        self.assertEqual(self.post(json.dumps(event(61, 9, 'native')).encode()), 200)
        self.assertEqual(self.post(json.dumps({**poll_event(62, 10), 'transport': 'udp'}).encode()), 400)
        self.assertEqual(self.post(json.dumps({**poll_event(60, 8), 'transport': 'wss'}).encode()), 409)
        self.assertEqual(self.post(json.dumps(poll_event(60, 8), separators=(',', ':')).encode(), signed=False), 401)
        self.assertEqual(self.rows('select participant_id,client_runtime,transport'
                                   ' from analytics_relay_participants order by participant_id'),
                         [(8, 'browser', 'https-poll'), (9, 'native', 'wss')])


if __name__ == '__main__':
    unittest.main()
