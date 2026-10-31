#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import play_parts  # noqa: E402


class PlayPartsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.play = Path(self.temp.name)
        self.data = bytes(range(256)) * 5000
        parts = ['dunecity.data.part-000', 'dunecity.data.part-001', 'dunecity.data.part-002']
        for index, name in enumerate(parts):
            (self.play / name).write_bytes(self.data[index * 500000:(index + 1) * 500000])
        self.build = {'artifacts': ['dunecity.data'], 'split': {'dunecity.data': parts},
                      'sha256': {'dunecity.data': hashlib.sha256(self.data).hexdigest()}}
        (self.play / 'build.json').write_text(json.dumps(self.build))

    def tearDown(self):
        self.temp.cleanup()

    def test_join_restores_bytes_in_order_and_removes_parts(self):
        self.assertEqual(play_parts.artifact_sha256(self.play, self.build, 'dunecity.data'),
                         self.build['sha256']['dunecity.data'])
        self.assertEqual(play_parts.join(self.play), ['dunecity.data'])
        self.assertEqual((self.play / 'dunecity.data').read_bytes(), self.data)
        self.assertEqual(list(self.play.glob('*.part-*')), [])

    def test_join_refuses_reordered_parts(self):
        self.build['split']['dunecity.data'].reverse()
        (self.play / 'build.json').write_text(json.dumps(self.build))
        with self.assertRaises(ValueError):
            play_parts.join(self.play)
        self.assertFalse((self.play / 'dunecity.data').exists())

    def test_rejects_part_names_outside_the_artifact(self):
        self.build['split']['dunecity.data'] = ['../dunecity.data.part-000']
        with self.assertRaises(ValueError):
            play_parts.split_parts(self.play, self.build, 'dunecity.data')


if __name__ == '__main__':
    unittest.main()
