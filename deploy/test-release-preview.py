#!/usr/bin/env python3
"""Exercise preview identity/routing boundaries and own-room-only readback without a server."""
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


preview = module('preview', ROOT / 'deploy/install-release-preview.py')
inspector = module('inspector', ROOT / 'deploy/inspect-release-preview.py')


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        shutil.copytree(ROOT / 'p2p-service', self.root / 'p2p-preview')
        manifest = json.loads((self.root / 'p2p-preview/SOURCE.json').read_text())
        self.source = manifest['commit']
        self.name = 'play-test-865-' + self.source[:9]
        self.website = self.root / 'website'
        self.play = self.website / self.name
        (self.play / 'p2p').mkdir(parents=True)
        (self.root / 'deploy').mkdir()
        self.descriptor = {'path': self.name, 'sourceCommit': self.source,
                           'version': '1.0.865', 'gameProtocol': 122}
        self.write_descriptor()
        (self.play / 'build.json').write_text(json.dumps({'version': '1.0.865', 'sourceCommit': self.source}))
        shutil.copyfile(ROOT / 'deploy/p2p-preview-entry.php', self.root / 'deploy/p2p-preview-entry.php')
        shutil.copyfile(ROOT / 'deploy/p2p-preview-entry.php', self.play / 'p2p/index.php')
        shutil.copyfile(self.root / 'p2p-preview/public/.htaccess', self.play / 'p2p/.htaccess')

    def write_descriptor(self):
        (self.root / 'deploy/release-preview.json').write_text(json.dumps(self.descriptor))

    def test_matching_metadata_accepted_without_touching_production(self):
        self.assertEqual(preview.preview_spec(self.root, self.website), self.descriptor)
        self.assertFalse((self.website / 'play').exists())
        self.assertFalse((self.website / 'p2p').exists())

    def test_browser_source_mismatch_refused(self):
        (self.play / 'build.json').write_text(json.dumps({'version': '1.0.865', 'sourceCommit': '0' * 40}))
        with self.assertRaisesRegex(ValueError, 'commits must match'):
            preview.preview_spec(self.root, self.website)

    def test_browser_version_mismatch_refused(self):
        (self.play / 'build.json').write_text(json.dumps({'version': '1.0.864', 'sourceCommit': self.source}))
        with self.assertRaisesRegex(ValueError, 'version must match'):
            preview.preview_spec(self.root, self.website)

    def test_routes_and_entrypoint_drift_refused(self):
        (self.play / 'p2p/.htaccess').write_text('different route')
        with self.assertRaisesRegex(ValueError, 'routes'):
            preview.preview_spec(self.root, self.website)
        shutil.copyfile(self.root / 'p2p-preview/public/.htaccess', self.play / 'p2p/.htaccess')
        (self.play / 'p2p/index.php').write_text('<?php // drift')
        with self.assertRaisesRegex(ValueError, 'entrypoint'):
            preview.preview_spec(self.root, self.website)

    def test_unsafe_or_unpinned_names_and_protocols_refused(self):
        for name in ('play', '../play-test-escape', 'play-test-865-deadbeef0'):
            self.descriptor['path'] = name
            self.write_descriptor()
            with self.assertRaisesRegex(ValueError, 'path'):
                preview.preview_spec(self.root, self.website)
        self.descriptor['path'] = self.name
        for protocol in (0, -1, 65536, True, '122'):
            self.descriptor['gameProtocol'] = protocol
            self.write_descriptor()
            with self.assertRaisesRegex(ValueError, 'protocol'):
                preview.preview_spec(self.root, self.website)

    def test_readback_selects_only_own_room_and_utc_window(self):
        database = self.root / 'games.sqlite'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE analytics_relay_events (' + ','.join(inspector.COLUMNS.split(',')) + ')')
            for room, occurred in [('own', 100), ('own', 999), ('other', 100)]:
                db.execute('INSERT INTO analytics_relay_events VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (room, str(occurred) + room, 'joined', occurred, 1, 'browser', 'gate865-test',
                            '', 'direct-p2p', 'signaling_service_v1'))
        rows = inspector.read_rows(database, 'own', 90, 110)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['room_id'], 'own')
        self.assertEqual(rows[0]['occurred_at'], 100)

    def test_app_version_window_correlation_refuses_ambiguous_or_missing_rooms(self):
        database = self.root / 'correlation.sqlite'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE analytics_relay_events (' + ','.join(inspector.COLUMNS.split(',')) + ')')
            for room, version, occurred in [('own', 'DuneCity1.0.865', 100),
                                             ('own', 'DuneCity1.0.865', 101),
                                             ('other', 'different', 100),
                                             ('later', 'DuneCity1.0.865', 200)]:
                db.execute('INSERT INTO analytics_relay_events VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (room, str(occurred) + room, 'joined', occurred, 1, 'browser', version,
                            '', 'direct-p2p', 'signaling_service_v1'))
        self.assertEqual(inspector.own_room(database, 'DuneCity1.0.865', 90, 110), 'own')
        for since, until in [(90, 210), (300, 310)]:
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                inspector.own_room(database, 'DuneCity1.0.865', since, until)


if __name__ == '__main__':
    unittest.main()
