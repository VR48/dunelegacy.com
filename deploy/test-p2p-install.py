#!/usr/bin/env python3
"""Exercise install integrity, repeat installs, operator config and failed-update preservation."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", root / "deploy/install-p2p-service.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_atomic_integrity_and_config_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source, target = base / "source", base / "private"
            shutil.copytree(root / "p2p-service", source)
            first = installer.install(source, target)
            self.assertEqual((target / "current").resolve(), target / "releases" / first)
            self.assertEqual((target / "run").stat().st_mode & 0o7777, 0o2770)
            config = target / "config.php"
            config.write_text(config.read_text() + "// operator configuration preserved\n")
            self.assertEqual(installer.install(source, target), first)
            self.assertIn("operator configuration preserved", config.read_text())
            (source / "src/Rooms.php").write_text("<?php // altered")
            with self.assertRaisesRegex(ValueError, "manifest mismatch"):
                installer.install(source, target)
            self.assertEqual((target / "current").resolve(), target / "releases" / first)


if __name__ == "__main__":
    unittest.main()
