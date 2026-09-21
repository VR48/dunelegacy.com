#!/usr/bin/env python3
"""Prevent stale/corrupt encodings from being paired with a new game release."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('install-play-transfer.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'encoded'
        self.play = Path(self.temp.name) / 'play'
        self.source.mkdir()
        self.play.mkdir()
        self.manifest = {}
        for name in installer.NAMES:
            raw, encoded = name.encode(), b'encoded-' + name.encode()
            (self.play / name).write_bytes(raw)
            (self.source / (name + '.br')).write_bytes(encoded)
            self.manifest[name] = {
                'sourceSha256': hashlib.sha256(raw).hexdigest(),
                'encodedSha256': hashlib.sha256(encoded).hexdigest(),
            }
        (self.play / '.htaccess').write_text('Options -Indexes\n')
        (self.source / 'transfer.json').write_text(json.dumps(self.manifest))

    def test_matching_release_installs_and_preserves_existing_rules(self):
        installer.install(self.source, self.play)
        self.assertEqual(len(list(self.play.glob('*.br'))), len(installer.NAMES))
        self.assertTrue((self.play / '.htaccess').read_text().startswith('Options -Indexes\n'))

    def test_new_release_fails_before_installing_any_encoding(self):
        (self.play / 'shell.css').write_text('new release')
        with self.assertRaisesRegex(ValueError, 'release changed'):
            installer.install(self.source, self.play)
        self.assertEqual(list(self.play.glob('*.br')), [])

    def test_corruption_fails_before_installing_any_encoding(self):
        (self.source / 'shell.css.br').write_bytes(b'truncated')
        with self.assertRaisesRegex(ValueError, 'Corrupt'):
            installer.install(self.source, self.play)
        self.assertEqual(list(self.play.glob('*.br')), [])

    def test_unexpected_manifest_entry_rejected(self):
        self.manifest['../outside'] = {}
        (self.source / 'transfer.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            installer.install(self.source, self.play)


if __name__ == '__main__':
    unittest.main()
