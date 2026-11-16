import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('changelog', Path(__file__).parents[1] / 'build-changelog.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

RELEASES = [('v1.0.1', '2026-01-01'), ('v1.0.2', '2026-01-14'), ('v1.0.3', '2026-01-15'), ('v1.0.4', '2026-01-20')]


class ChangelogGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = root / 'game'
        notes = self.repo / 'releases' / 'desktop'
        notes.mkdir(parents=True)
        for tag, _ in RELEASES:
            (notes / (tag[1:] + '.md')).write_text(f'# {tag}\n\n- Change in {tag}\n', encoding='utf-8')
        self.groups = root / 'changelog'
        self.groups.mkdir()
        (self.groups / 'mid.md').write_text('# Summary\n\n- Both releases together\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_group_is_one_entry_that_keeps_every_release_and_anchor(self):
        group = [{'tags': ['v1.0.2', 'v1.0.3'], 'title': 'Dune City 1.0.2 to 1.0.3', 'notes': 'mid.md'}]
        page = m.render(self.repo, RELEASES, group, self.groups)
        self.assertEqual(page.count('<section'), 3)
        self.assertIn('id="v1.0.3-group"', page)
        self.assertIn('14–15 January 2026', page)
        self.assertIn('Both releases together', page)
        for tag in ('v1.0.2', 'v1.0.3'):
            self.assertIn(f'<details class="release-part" id="{tag}">', page)
            self.assertIn(f'Change in {tag}', page)
        self.assertIn('compare/v1.0.1...v1.0.3', page)
        self.assertLess(page.index('v1.0.4'), page.index('v1.0.3-group'))
        self.assertLess(page.index('id="v1.0.3"'), page.index('id="v1.0.2"'))

    def test_without_groups_every_release_has_its_own_entry(self):
        page = m.render(self.repo, RELEASES)
        self.assertEqual(page.count('<section'), 4)
        self.assertNotIn('<details', page)

    def test_group_with_a_gap_is_refused(self):
        with self.assertRaises(ValueError):
            m.render(self.repo, RELEASES, [{'tags': ['v1.0.1', 'v1.0.3']}], self.groups)

    def test_shipped_group_file_is_valid(self):
        groups = m.load_groups(m.GROUPS)
        for group in groups:
            self.assertTrue((m.GROUPS.parent / group['notes']).is_file())
        self.assertEqual(json.loads(m.GROUPS.read_text())[0]['tags'][-1], 'v1.0.871')


if __name__ == '__main__':
    unittest.main()
