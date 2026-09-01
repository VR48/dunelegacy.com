#!/usr/bin/env python3
"""Validate the committed browser package and its security policy."""

from __future__ import annotations

import hashlib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


class PlayPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._inline_script = False
        self._inline_script_has_content = False
        self.meta_csp = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for name, _ in attrs:
            if name.lower().startswith("on"):
                self.errors.append(f"inline event handler is not allowed: {name}")
            if name.lower() == "style":
                self.errors.append("inline style attribute is not allowed")

        if tag == "style":
            self.errors.append("inline <style> is not allowed")
        elif tag == "script":
            source = values.get("src")
            self._inline_script = source is None
            self._inline_script_has_content = False
            if source:
                parsed = urlparse(source)
                if parsed.scheme or parsed.netloc or source.startswith("//"):
                    self.errors.append(f"external script source is not allowed: {source}")
        elif tag == "meta" and values.get("http-equiv", "").lower() == "content-security-policy":
            self.meta_csp = values.get("content", "")

    def handle_data(self, data: str) -> None:
        if self._inline_script and data.strip():
            self._inline_script_has_content = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._inline_script:
            if self._inline_script_has_content:
                self.errors.append("inline script is not allowed")
            self._inline_script = False


def require_tokens(path: Path, tokens: tuple[str, ...]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [f"{path}: missing {token}" for token in tokens if token not in text]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    website = repo_root / "website"
    play = website / "play"
    errors: list[str] = []

    errors.extend(require_tokens(website / ".htaccess", (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "X-Frame-Options",
        "Permissions-Policy",
        "Header always set Server \"Apache\"",
        "Options -Indexes",
    )))
    errors.extend(require_tokens(play / ".htaccess", (
        "Content-Security-Policy",
        "default-src 'none'",
        "frame-ancestors 'none'",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
        "Cache-Control \"no-store\"",
    )))

    parser = PlayPageParser()
    parser.feed((play / "index.html").read_text(encoding="utf-8"))
    errors.extend(parser.errors)
    for token in ("default-src 'none'", "script-src 'self' 'wasm-unsafe-eval'", "style-src 'self'"):
        if token not in parser.meta_csp:
            errors.append(f"website/play/index.html: meta CSP is missing {token}")

    build = json.loads((play / "build.json").read_text(encoding="utf-8-sig"))
    required_artifacts = {
        "index.html", "shell.css", "shell.js",
        "dunecity.js", "dunecity.wasm", "dunecity.data",
    }
    artifact_names = build.get("artifacts")
    if not isinstance(artifact_names, list):
        errors.append("website/play/build.json: artifacts must be a list")
        artifact_names = []
    else:
        missing = required_artifacts.difference(artifact_names)
        if missing:
            errors.append(f"website/play/build.json: missing required artifacts: {sorted(missing)}")

    expected_hashes = build.get("sha256")
    if not isinstance(expected_hashes, dict) or not expected_hashes:
        errors.append("website/play/build.json: missing artifact SHA-256 map")
    else:
        for name in artifact_names:
            artifact = play / name
            if not artifact.is_file():
                errors.append(f"missing browser artifact: {name}")
                continue
            expected = expected_hashes.get(name)
            actual = sha256(artifact)
            if expected != actual:
                errors.append(f"SHA-256 mismatch for {name}: expected {expected}, got {actual}")

    if errors:
        print("Browser security validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Browser security policy and artifact hashes are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
