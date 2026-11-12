#!/usr/bin/env python3
"""Install verified signaling source outside Apache's document root without administrator access."""
from __future__ import annotations
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def install(source: Path, target: Path, group: str | None = None, *,
            base_path: str = "/p2p", game_protocol: int = 0) -> str:
    if base_path not in ("/p2p", "/play-staging/p2p") and not re.fullmatch(r"/play-test-[a-z0-9-]{1,80}/p2p", base_path):
        raise ValueError("Invalid signaling base path")
    if not isinstance(game_protocol, int) or not 0 <= game_protocol <= 65535:
        raise ValueError("Invalid game protocol")
    source = source.resolve(strict=True)
    manifest_bytes = (source / "SOURCE.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("commit", "")):
        raise ValueError("Invalid source commit")
    actual = {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()}
    expected = manifest["sha256"]
    if actual != set(expected) | {"SOURCE.json"}:
        raise ValueError("Unexpected or missing service files")
    for name, digest in expected.items():
        path = source / name
        if (not re.fullmatch(r"(?:src|public)/[A-Za-z0-9_.-]+", name)
                or path.is_symlink() or not path.resolve().is_relative_to(source)
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError("Service manifest mismatch")
        if path.suffix == ".php":
            subprocess.run(["php", "-l", str(path)], check=True, capture_output=True)
    if target.is_symlink():
        raise ValueError("Private deployment root must not be a symlink")
    target.mkdir(parents=True, exist_ok=True, mode=0o750)
    target = target.resolve(strict=True)
    gid = grp.getgrnam(group).gr_gid if group else os.getgid()

    def mode(path: Path, permissions: int) -> None:
        os.chown(path, -1, gid)
        path.chmod(permissions)

    mode(target, 0o2750)
    for directory, permissions in (("releases", 0o2750), ("run", 0o2770)):
        path = target / directory
        if path.is_symlink():
            raise ValueError("Private directory must not be a symlink")
        path.mkdir(exist_ok=True)
        mode(path, permissions)

    # Configuration contains no credentials and is created once; keep operator changes.
    config = target / "config.php"
    if config.is_symlink():
        raise ValueError("Configuration must not be a symlink")
    if not config.exists():
        state = str(target / "run/state").replace("\\", "\\\\").replace("'", "\\'")
        origins = ("'https://dunelegacy.com', 'https://www.dunelegacy.com'"
                   if base_path == "/p2p" else "'https://dunelegacy.com'")
        with config.open("x") as handle:
            handle.write("<?php\nreturn [\n"
                         f"'state_dir' => '{state}',\n"
                         f"'public_base_url' => 'https://dunelegacy.com{base_path}',\n"
                         f"'base_path' => '{base_path}',\n"
                         f"'allowed_origins' => [{origins}],\n"
                         "'ice_servers' => ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'],\n"
                         "'allow_plaintext_loopback' => false,\n"
                         f"'app' => 'dunecity', 'required_game_protocol' => {game_protocol},\n"
                         "'analytics_enabled' => true, 'log_enabled' => true,\n];\n")
        mode(config, 0o640)
    subprocess.run(["php", "-l", str(config)], check=True, capture_output=True)
    release_id = hashlib.sha256(manifest_bytes).hexdigest()
    release = target / "releases" / release_id
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=target / "releases"))
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True)
        for path in [staging, *staging.rglob("*")]:
            mode(path, 0o750 if path.is_dir() else 0o640)
        # Verify an existing immutable release rather than silently using altered files.
        if release.exists():
            for name in [*expected, "SOURCE.json"]:
                if (release / name).read_bytes() != (staging / name).read_bytes():
                    raise ValueError("Previously installed release has changed")
        else:
            staging.rename(release)
        link = target / f".current-{os.getpid()}"
        link.symlink_to(Path("releases") / release_id)
        link.replace(target / "current")
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return release_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path("/var/www/data/dunecity-p2p"))
    parser.add_argument("--group")
    parser.add_argument("--base-path", default="/p2p")
    parser.add_argument("--game-protocol", type=int, default=0)
    args = parser.parse_args()
    os.umask(0o027)
    print("Installed signaling source " + install(
        args.source, args.target, args.group,
        base_path=args.base_path, game_protocol=args.game_protocol))
