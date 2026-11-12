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

    def test_preview_configuration_is_separate_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source, stable, preview = base / "source", base / "stable", base / "preview"
            shutil.copytree(root / "p2p-service", source)
            installer.install(source, stable)
            stable_config = (stable / "config.php").read_bytes()
            kwargs = {"base_path": "/play-test-865-249aa55e5/p2p", "game_protocol": 122}
            installer.install(source, preview, **kwargs)
            preview_config = (preview / "config.php").read_text()
            self.assertIn("'base_path' => '/play-test-865-249aa55e5/p2p'", preview_config)
            self.assertIn("'required_game_protocol' => 122", preview_config)
            self.assertIn("'allowed_origins' => ['https://dunelegacy.com']", preview_config)
            self.assertNotIn(str(stable), preview_config)
            (preview / "config.php").write_text(preview_config + "// preserve operator config\n")
            installer.install(source, preview, **kwargs)
            self.assertIn("preserve operator config", (preview / "config.php").read_text())
            self.assertEqual((stable / "config.php").read_bytes(), stable_config)

    def test_preview_rejects_unsafe_paths_and_protocols(self):
        for path in ("/p2p/../other", "/play", "';bad"):
            with self.assertRaisesRegex(ValueError, "base path"):
                installer.install(Path("unused"), Path("unused"), base_path=path)
        for protocol in (-1, 65536):
            with self.assertRaisesRegex(ValueError, "protocol"):
                installer.install(Path("unused"), Path("unused"), game_protocol=protocol)


if __name__ == "__main__":
    unittest.main()
