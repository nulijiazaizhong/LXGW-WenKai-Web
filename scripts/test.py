#!/usr/bin/env python3
"""Validate dist/ WebFont packaging artifacts.

Checks:
  1. Required files exist (woff2 / style.css / metadata.json / sha256.txt)
  2. Each WOFF2 opens with fontTools
  3. flavor == "woff2"
  4. name table contains the LXGW WenKai family name
  5. Glyph counts are non-trivial
  6. CSS has font-family / weight / style / font-display / woff2 URLs
  7. sha256.txt hashes match recomputed hashes

Exit 0 on success, 1 on any failure (fail fast, no swallowed errors).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.common import (  # type: ignore[import-not-found]
        DIST_DIR,
        FONT_FAMILY_CSS,
        FONT_DISPLAY_NAME,
        BuildError,
        setup_logging,
        sha256_file,
    )
else:
    from .common import (
        DIST_DIR,
        FONT_FAMILY_CSS,
        FONT_DISPLAY_NAME,
        BuildError,
        setup_logging,
        sha256_file,
    )

MIN_GLYPHS = 1000
FAMILY_TOKENS = ("LXGW WenKai", "LXGW")


class TestFailure(Exception):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DIST_DIR,
    )
    parser.add_argument(
        "--allow-missing-weights",
        action="store_true",
        help="Do not require Light/Regular/Medium (any single face is enough)",
    )
    return parser.parse_args(argv)


def test_required_files(dist_dir: Path, log) -> list[Path]:
    required = ["style.css", "metadata.json", "sha256.txt"]
    for name in required:
        path = dist_dir / name
        if not path.is_file():
            raise TestFailure(f"Missing required file: {path}")
        if path.stat().st_size == 0:
            raise TestFailure(f"Required file is empty: {path}")
    woff2 = sorted(dist_dir.glob("*.woff2"))
    if not woff2:
        raise TestFailure(f"No .woff2 files in {dist_dir}")
    log.info("OK  required files present (%d woff2)", len(woff2))
    return woff2


def test_woff2_fonts(woff2_files: list[Path], log) -> list[dict]:
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise TestFailure(f"fontTools not installed: {exc}") from exc

    results = []
    for path in woff2_files:
        try:
            font = TTFont(path)
        except Exception as exc:
            raise TestFailure(f"{path.name}: fontTools cannot open file: {exc}") from exc

        if font.flavor != "woff2":
            font.close()
            raise TestFailure(f"{path.name}: flavor={font.flavor!r}, expected 'woff2'")

        try:
            names = []
            for record in font["name"].names:
                try:
                    names.append(record.toUnicode())
                except Exception:
                    continue
            joined = "\n".join(names)
            if not any(token in joined for token in FAMILY_TOKENS):
                raise TestFailure(
                    f"{path.name}: name table lacks expected family token "
                    f"{'/'.join(FAMILY_TOKENS)}"
                )
            family = font["name"].getDebugName(1) or font["name"].getDebugName(16) or ""
            weight = int(font["OS/2"].usWeightClass or 400)
            glyphs = int(font["maxp"].numGlyphs)
            if glyphs < MIN_GLYPHS:
                raise TestFailure(f"{path.name}: glyph count {glyphs} < {MIN_GLYPHS}")
        finally:
            font.close()

        log.info("OK  %s  flavor=woff2  family=%r  weight=%s  glyphs=%d", path.name, family, weight, glyphs)
        results.append({"file": path.name, "family": family, "weight": weight, "glyphs": glyphs})
    return results


def test_css(dist_dir: Path, woff2_files: list[Path], log) -> None:
    css_path = dist_dir / "style.css"
    css = css_path.read_text(encoding="utf-8")
    if "font-family" not in css:
        raise TestFailure("style.css missing font-family")
    if f'font-family: "{FONT_FAMILY_CSS}"' not in css and FONT_FAMILY_CSS not in css:
        raise TestFailure(f'style.css missing expected family "{FONT_FAMILY_CSS}"')
    if "font-display:" not in css:
        raise TestFailure("style.css missing font-display")
    if "font-display: swap" not in css:
        raise TestFailure("style.css must use font-display: swap")
    if "font-weight:" not in css:
        raise TestFailure("style.css missing font-weight")
    if "font-style:" not in css:
        raise TestFailure("style.css missing font-style")

    urls = re.findall(r'url\(["\']?(\./[^"\')]+)["\']?\)', css)
    if not urls:
        raise TestFailure("style.css has no woff2 url() references")
    for url in urls:
        if not url.endswith(".woff2"):
            raise TestFailure(f"style.css url is not woff2: {url}")
        if url.startswith("/") or "://" in url:
            raise TestFailure(f"style.css must use relative urls, got: {url}")
        ref = dist_dir / Path(url).name
        if not ref.is_file():
            raise TestFailure(f"style.css references missing file: {url}")

    expected_names = {p.name for p in woff2_files}
    referenced = {Path(u).name for u in urls}
    missing = expected_names - referenced
    if missing:
        raise TestFailure(f"style.css does not reference all woff2 faces: {sorted(missing)}")

    # Per-weight css modules (optional but expected when present in package).
    for woff2 in woff2_files:
        module = dist_dir / f"{woff2.stem.lower().replace('_', '-')}.css"
        if not module.is_file():
            # Not fatal for core tests; warn only if style.css exists alone.
            continue
        text = module.read_text(encoding="utf-8")
        if woff2.name not in text:
            raise TestFailure(f"{module.name} does not reference {woff2.name}")
        if "font-display: swap" not in text:
            raise TestFailure(f"{module.name} must use font-display: swap")

    log.info("OK  style.css (family, weights, font-display:swap, relative woff2 urls)")


def test_metadata(dist_dir: Path, log) -> None:
    path = dist_dir / "metadata.json"
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TestFailure(f"metadata.json is not valid JSON: {exc}") from exc

    for key in ("name", "webfont", "upstream", "license", "files"):
        if key not in metadata:
            raise TestFailure(f"metadata.json missing key: {key}")
    if not metadata["files"]:
        raise TestFailure("metadata.json files[] is empty")
    for entry in metadata["files"]:
        for key in ("file", "weight", "style"):
            if key not in entry:
                raise TestFailure(f"metadata.json files[] entry missing {key}: {entry}")
        fpath = dist_dir / str(entry["file"])
        if not fpath.is_file():
            raise TestFailure(f"metadata.json lists missing file: {entry['file']}")
    if "version" not in metadata["webfont"]:
        raise TestFailure("metadata.json webfont.version missing")
    if "version" not in metadata["upstream"]:
        raise TestFailure("metadata.json upstream.version missing")
    log.info(
        "OK  metadata.json  webfont=%s  upstream=%s  files=%d",
        metadata["webfont"].get("version"),
        metadata["upstream"].get("version"),
        len(metadata["files"]),
    )


def test_sha256(dist_dir: Path, log) -> None:
    path = dist_dir / "sha256.txt"
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise TestFailure("sha256.txt is empty")
    checked = 0
    for line_no, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Formats: "HASH  name" or "HASH *name"
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if not match:
            raise TestFailure(f"sha256.txt:{line_no} malformed line: {line!r}")
        expected, name = match.group(1).lower(), match.group(2).strip()
        file_path = dist_dir / name
        if not file_path.is_file():
            raise TestFailure(f"sha256.txt lists missing file: {name}")
        actual = sha256_file(file_path)
        if actual != expected:
            raise TestFailure(f"sha256 mismatch for {name}: file={actual} manifest={expected}")
        checked += 1
    if checked == 0:
        raise TestFailure("sha256.txt has no hash lines")
    log.info("OK  sha256.txt (%d file hashes verified)", checked)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = setup_logging(args.verbose)
    dist_dir: Path = args.dist_dir

    try:
        if not dist_dir.is_dir():
            raise TestFailure(f"dist dir missing: {dist_dir}")
        woff2_files = test_required_files(dist_dir, log)
        fonts = test_woff2_fonts(woff2_files, log)

        if not args.allow_missing_weights:
            weights = {f["weight"] for f in fonts}
            expected = {300, 400, 500}
            missing = expected - weights
            if missing:
                log.warning(
                    "Some phase-1 weights are missing: %s (present: %s)",
                    sorted(missing),
                    sorted(weights),
                )
            if not weights:
                raise TestFailure("No weights discovered in woff2 faces")

        test_css(dist_dir, woff2_files, log)
        test_metadata(dist_dir, log)
        test_sha256(dist_dir, log)
    except TestFailure as exc:
        log.error("TEST FAILED: %s", exc)
        return 1
    except BuildError as exc:
        log.error("TEST FAILED: %s", exc)
        return 1

    log.info("All tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
