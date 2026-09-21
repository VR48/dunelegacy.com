#!/usr/bin/env python3
"""Exercise the web-root rsync contract: stable validators for unchanged bytes, plus content updates, deletions and .well-known preservation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

root = Path(__file__).resolve().parents[1]
script = root / "deploy/deploy-release.sh"

OLD_MTIME = 1600000000
NEW_MTIME = 1700000000


def sync_args():
    """Read the rsync flags the deploy script actually uses, without running the deployment."""
    assignment = next(
        line for line in script.read_text().splitlines() if line.startswith("WEB_SYNC_ARGS=(")
    )
    printed = subprocess.run(
        ["bash", "-c", assignment + '\nprintf "%s\\n" "${WEB_SYNC_ARGS[@]}"'],
        check=True,
        capture_output=True,
        text=True,
    )
    return printed.stdout.split()


def compatible_rsync(args):
    """Pick an rsync that understands the deploy flags; macOS ships openrsync, which does not."""
    candidates = [
        shutil.which("rsync"),
        "/opt/homebrew/bin/rsync",
        "/usr/local/bin/rsync",
        "/usr/bin/rsync",
    ]
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory)
        (probe / "from").mkdir()
        (probe / "to").mkdir()
        for candidate in candidates:
            if not candidate or not os.access(candidate, os.X_OK):
                continue
            probed = subprocess.run(
                [candidate, "--dry-run", *args, f"{probe / 'from'}/", f"{probe / 'to'}/"],
                capture_output=True,
                text=True,
            )
            if probed.returncode == 0:
                return candidate
    return None


class WebSyncTests(unittest.TestCase):
    def setUp(self):
        self.args = sync_args()
        self.rsync = compatible_rsync(self.args)
        if not self.rsync:
            if os.environ.get("CI"):
                self.fail("CI must provide rsync supporting the deployment flags")
            self.skipTest("no rsync supporting %s found" % " ".join(self.args))

    def deploy(self, staging, web_root):
        subprocess.run([self.rsync, *self.args, f"{staging}/", f"{web_root}/"], check=True)

    def stage(self, base, name, files):
        """Build a staging snapshot; git archive stamps every file with the deploy time."""
        staging = base / name
        (staging / "game").mkdir(parents=True)
        for relative, (content, mtime) in files.items():
            path = staging / relative
            path.write_bytes(content)
            os.utime(path, (mtime, mtime))
        return staging

    def test_unchanged_bytes_keep_validators_while_changes_and_deletions_apply(self):
        self.assertIn("--checksum", self.args)
        self.assertIn("--no-times", self.args)
        self.assertLess(self.args.index("-a"), self.args.index("--no-times"))

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            web_root = base / "web"
            (web_root / ".well-known/acme-challenge").mkdir(parents=True)
            (web_root / ".well-known/acme-challenge/token").write_bytes(b"acme")

            first = self.stage(
                base,
                "first",
                {
                    "game/dunecity-v1.0.737.zip": (b"GAMEDATA" * 512, OLD_MTIME),
                    "game/manifest.json": (b'{"build":1}', OLD_MTIME),
                    "stats.html": (b"<p>10 downloads</p>", OLD_MTIME),
                    "retired.html": (b"<p>old page</p>", OLD_MTIME),
                },
            )
            self.deploy(first, web_root)
            game = web_root / "game/dunecity-v1.0.737.zip"
            deployed_mtime = game.stat().st_mtime

            # Second deploy: same release, refreshed stats, all staged mtimes bumped.
            second = self.stage(
                base,
                "second",
                {
                    "game/dunecity-v1.0.737.zip": (b"GAMEDATA" * 512, NEW_MTIME),
                    "game/manifest.json": (b'{"build":2}', NEW_MTIME),
                    "stats.html": (b"<p>11 downloads</p>", NEW_MTIME),
                    "news.html": (b"<p>new page</p>", NEW_MTIME),
                },
            )
            self.deploy(second, web_root)

            # Byte-identical game data keeps its size+mtime ETag, so browsers revalidate instead of refetching.
            self.assertEqual(game.read_bytes(), b"GAMEDATA" * 512)
            self.assertEqual(game.stat().st_mtime, deployed_mtime)
            # Changed content still lands, including a same-length change the quick check would miss.
            manifest = web_root / "game/manifest.json"
            self.assertEqual(manifest.read_bytes(), b'{"build":2}')
            self.assertEqual(len(b'{"build":1}'), len(b'{"build":2}'))
            self.assertEqual((web_root / "stats.html").read_bytes(), b"<p>11 downloads</p>")
            self.assertEqual((web_root / "news.html").read_bytes(), b"<p>new page</p>")
            self.assertFalse((web_root / "retired.html").exists())
            self.assertEqual((web_root / ".well-known/acme-challenge/token").read_bytes(), b"acme")


if __name__ == "__main__":
    unittest.main()
