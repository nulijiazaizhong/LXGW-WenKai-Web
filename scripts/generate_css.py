#!/usr/bin/env python3
"""Generate dist/style.css from packaged WOFF2 face metadata.

Font URLs stay relative (./Name.woff2) so GitHub raw and jsDelivr both work
without hardcoding owner/repo/CDN domains.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.common import (  # type: ignore[import-not-found]
        DIST_DIR,
        FONT_FAMILY_CSS,
        BuildError,
        setup_logging,
    )
else:
    from .common import (
        DIST_DIR,
        FONT_FAMILY_CSS,
        BuildError,
        setup_logging,
    )


def render_css(
    faces: Iterable[dict[str, Any]],
    *,
    family: str = FONT_FAMILY_CSS,
    font_display: str = "swap",
) -> str:
    lines: list[str] = [
        "/*!",
        " * LXGW WenKai WebFont packaging project.",
        " * Original font by LXGW — https://github.com/lxgw/LxgwWenKai",
        " * SIL Open Font License 1.1 (OFL-1.1).",
        " * This file is auto-generated. Do not edit by hand.",
        " */",
        "",
    ]
    for face in faces:
        weight = int(face["weight"])
        style = str(face.get("style") or "normal")
        filename = str(face["file"])
        if not filename.endswith(".woff2"):
            raise BuildError(f"CSS face file must be .woff2, got {filename!r}")
        lines.extend(
            [
                "@font-face {",
                f'  font-family: "{family}";',
                f"  font-style: {style};",
                f"  font-weight: {weight};",
                f"  font-display: {font_display};",
                f'  src: url("./{filename}") format("woff2");',
                "}",
                "",
            ]
        )
    if len(lines) <= 6:
        raise BuildError("No faces provided for CSS generation")
    return "\n".join(lines)


def write_css(
    faces: list[dict[str, Any]],
    dest: Path | None = None,
    *,
    family: str = FONT_FAMILY_CSS,
    font_display: str = "swap",
) -> Path:
    dest = dest or (DIST_DIR / "style.css")
    dest.parent.mkdir(parents=True, exist_ok=True)
    css = render_css(faces, family=family, font_display=font_display)
    dest.write_text(css, encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--from-metadata",
        type=Path,
        default=DIST_DIR / "metadata.json",
        help="metadata.json produced by build.py",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DIST_DIR / "style.css",
    )
    args = parser.parse_args(argv)
    log = setup_logging(args.verbose)

    import json

    if not args.from_metadata.is_file():
        log.error("metadata not found: %s (run build.py first)", args.from_metadata)
        return 1
    metadata = json.loads(args.from_metadata.read_text(encoding="utf-8"))
    faces = metadata.get("files") or []
    # Keep only font faces (skip css/metadata/sha256 if listed).
    font_faces = [f for f in faces if str(f.get("file", "")).endswith(".woff2")]
    path = write_css(font_faces, args.output)
    log.info("Wrote %s (%d faces)", path, len(font_faces))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
