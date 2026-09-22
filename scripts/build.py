#!/usr/bin/env python3
"""Build full WOFF2 WebFonts + CSS + metadata.json + sha256.txt from build/source/.

Pipeline:
  TTF/OTF  ->  fontTools (save with flavor=woff2)  ->  dist/*.woff2
                                                    ->  dist/style.css
                                                    ->  dist/metadata.json
                                                    ->  dist/sha256.txt

Full fonts only in the default build (no aggressive CJK subsetting).
Optional subsetting is available via --subset, off by default.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.common import (  # type: ignore[import-not-found]
        DIST_DIR,
        FONT_DISPLAY_NAME,
        FONT_FAMILY_CSS,
        LICENSE_ID,
        PHASE1_EXPECTED_WEIGHTS,
        SOURCE_DIR,
        UPSTREAM_REPO,
        UPSTREAM_WEB,
        BuildError,
        FontFaceInfo,
        ensure_dirs,
        is_mono_filename,
        output_face_name,
        read_upstream_version,
        read_webfont_version,
        setup_logging,
        sha256_file,
        utc_now_iso,
        write_upstream_version,
    )
    from scripts.generate_css import write_css  # type: ignore[import-not-found]
else:
    from .common import (
        DIST_DIR,
        FONT_DISPLAY_NAME,
        FONT_FAMILY_CSS,
        LICENSE_ID,
        PHASE1_EXPECTED_WEIGHTS,
        SOURCE_DIR,
        UPSTREAM_REPO,
        UPSTREAM_WEB,
        BuildError,
        FontFaceInfo,
        ensure_dirs,
        is_mono_filename,
        output_face_name,
        read_upstream_version,
        read_webfont_version,
        setup_logging,
        sha256_file,
        utc_now_iso,
        write_upstream_version,
    )
    from .generate_css import write_css

SOURCE_MANIFEST = SOURCE_DIR / "source_manifest.json"
MIN_GLYPHS = 1000  # full CJK face must not collapse to near-empty after convert


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=SOURCE_DIR,
        help="Directory containing downloaded TTF/OTF sources",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DIST_DIR,
    )
    parser.add_argument(
        "--subset",
        action="store_true",
        help="EXPERIMENTAL: also emit dist/subsets/*.woff2 (full fonts still generated)",
    )
    parser.add_argument(
        "--update-upstream-version",
        action="store_true",
        help="Write UPSTREAM_VERSION from source_manifest after a successful build",
    )
    parser.add_argument(
        "--skip-css",
        action="store_true",
        help="Do not regenerate style.css (metadata/sha256 still written)",
    )
    return parser.parse_args(argv)


def _name_record(font: Any, name_id: int) -> str:
    name_table = font["name"]
    for platform_id, lang in ((3, 0x409), (1, 0), (3, 0)):
        value = name_table.getDebugName(name_id) if platform_id == 3 and lang == 0x409 else None
        if value:
            return value
    # Fallback: any record for this name ID.
    for record in name_table.names:
        if record.nameID == name_id:
            try:
                return record.toUnicode()
            except Exception:
                continue
    return ""


def inspect_font(path: Path) -> FontFaceInfo:
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:  # pragma: no cover
        raise BuildError("fontTools is required (pip install -r requirements.txt)") from exc

    try:
        font = TTFont(path, fontNumber=0, lazy=True)
    except Exception as exc:
        raise BuildError(f"Cannot open font {path.name}: {exc}") from exc

    try:
        family = _name_record(font, 16) or _name_record(font, 1)
        subfamily = _name_record(font, 17) or _name_record(font, 2)
        full_name = _name_record(font, 4) or f"{family} {subfamily}".strip()
        os2 = font["OS/2"]
        weight_class = int(getattr(os2, "usWeightClass", 400) or 400)
        if weight_class <= 0:
            weight_class = 400
        # Clamp to CSS weight range 100–900 in steps of 100.
        weight_class = max(100, min(900, int(round(weight_class / 100.0)) * 100))
        post = font["post"]
        is_italic = bool(getattr(post, "italicAngle", 0)) or "italic" in subfamily.lower()
        glyph_count = font["maxp"].numGlyphs
    except Exception as exc:
        raise BuildError(f"Font metadata unreadable in {path.name}: {exc}") from exc
    finally:
        font.close()

    is_mono = is_mono_filename(path.name) or "Mono" in family
    family_stem = "LXGWWenKaiMono" if is_mono else "LXGWWenKai"
    style = "italic" if is_italic else "normal"
    out = output_face_name(family_stem, weight_class, style)

    return FontFaceInfo(
        path=path,
        family=family or FONT_DISPLAY_NAME,
        subfamily=subfamily or "Regular",
        full_name=full_name,
        weight_class=weight_class,
        is_italic=is_italic,
        is_mono=is_mono,
        glyph_count=glyph_count,
        output_name=out,
    )


def convert_to_woff2(src: Path, dest: Path) -> None:
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:  # pragma: no cover
        raise BuildError("fontTools is required (pip install -r requirements.txt)") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.woff2")
    try:
        font = TTFont(src, recalcBBoxes=False, recalcTimestamp=False)
        # Do not rewrite name table or glyphs — packaging only.
        font.flavor = "woff2"
        font.save(tmp)
        font.close()
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise BuildError(f"WOFF2 conversion failed for {src.name}: {exc}") from exc

    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise BuildError(f"WOFF2 output empty for {src.name}")

    if dest.exists():
        dest.unlink()
    tmp.replace(dest)


def convert_subset(src: Path, dest: Path, text: str) -> None:
    """Optional second-phase subset. Full fonts remain the default product."""
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError as exc:  # pragma: no cover
        raise BuildError("fontTools is required") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.notdef_outline = True
    options.recalc_bounds = True
    options.canonical_order = True

    font = subset.load_font(str(src), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    subset.save_font(font, str(dest), options)
    font.close()


def write_metadata(
    dist_dir: Path,
    faces: list[FontFaceInfo],
    face_files: list[dict[str, Any]],
    *,
    source_manifest: dict[str, Any] | None,
) -> Path:
    webfont_version = read_webfont_version()
    upstream_version = (
        (source_manifest or {}).get("version") or read_upstream_version() or "unknown"
    )
    upstream_commit = (source_manifest or {}).get("commit_sha") or None
    upstream_tag = (source_manifest or {}).get("tag_name") or None

    try:
        import fontTools

        fonttools_version = fontTools.__version__
    except Exception:
        fonttools_version = "unknown"

    metadata: dict[str, Any] = {
        "name": FONT_DISPLAY_NAME,
        "webfont": {
            "version": webfont_version,
            "built_at": utc_now_iso(),
            "python_version": platform.python_version(),
            "fonttools_version": fonttools_version,
            "builder": "lxgw-wenkai-webfont",
        },
        "upstream": {
            "repository": UPSTREAM_REPO,
            "url": UPSTREAM_WEB,
            "version": str(upstream_version),
            "tag": upstream_tag,
            "commit": upstream_commit,
        },
        "license": LICENSE_ID,
        "css": {
            "file": "style.css",
            "family": FONT_FAMILY_CSS,
            "font_display": "swap",
            "url_style": "relative",
        },
        "files": face_files,
    }

    path = dist_dir / "metadata.json"
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_sha256(dist_dir: Path) -> Path:
    lines: list[str] = []
    for path in sorted(dist_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        if path.name == "sha256.txt":
            continue
        if path.suffix.lower() not in {".woff2", ".css", ".json", ".txt"}:
            continue
        digest = sha256_file(path)
        lines.append(f"{digest}  {path.name}")
    out = dist_dir / "sha256.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = setup_logging(args.verbose)
    ensure_dirs()

    source_dir: Path = args.source_dir
    dist_dir: Path = args.dist_dir
    dist_dir.mkdir(parents=True, exist_ok=True)

    source_manifest: dict[str, Any] | None = None
    if SOURCE_MANIFEST.is_file():
        source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))

    sources = sorted(
        [p for p in source_dir.iterdir() if p.suffix.lower() in {".ttf", ".otf"} and p.is_file()],
        key=lambda p: p.name.lower(),
    )
    if not sources:
        log.error("No TTF/OTF sources in %s. Run: python scripts/download.py", source_dir)
        return 1

    log.info("Building %d source font(s)…", len(sources))

    faces: list[FontFaceInfo] = []
    face_files: list[dict[str, Any]] = []
    seen_outputs: dict[str, Path] = {}

    for src in sources:
        info = inspect_font(src)
        if info.glyph_count < MIN_GLYPHS:
            raise BuildError(
                f"{src.name}: glyph count {info.glyph_count} < {MIN_GLYPHS}; refusing to package empty/corrupt font"
            )
        if info.output_name in seen_outputs:
            raise BuildError(
                f"Output name collision: {info.output_name} from {src.name} and {seen_outputs[info.output_name].name}"
            )
        seen_outputs[info.output_name] = src

        out_name = f"{info.output_name}.woff2"
        dest = dist_dir / out_name
        log.info(
            "Convert %s -> %s  (family=%r weight=%d glyphs=%d)",
            src.name,
            out_name,
            info.family,
            info.weight_class,
            info.glyph_count,
        )
        convert_to_woff2(src, dest)

        # Verify conversion is readable WOFF2.
        try:
            from fontTools.ttLib import TTFont

            check = TTFont(dest)
            if check.flavor != "woff2":
                raise BuildError(f"{out_name}: flavor is {check.flavor!r}, expected 'woff2'")
            if check["maxp"].numGlyphs < MIN_GLYPHS:
                raise BuildError(f"{out_name}: glyph count too low after conversion")
            check.close()
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError(f"{out_name}: cannot re-open after conversion: {exc}") from exc

        faces.append(info)
        face_files.append(
            {
                "file": out_name,
                "family": info.family,
                "subfamily": info.subfamily,
                "weight": info.weight_class,
                "style": info.style,
                "glyph_count": info.glyph_count,
                "size": dest.stat().st_size,
                "sha256": sha256_file(dest),
                "source": src.name,
            }
        )

    # Soft check for phase-1 expected weights (do not invent missing faces).
    found_weights = {f.weight_class for f in faces if not f.is_mono}
    for expected in PHASE1_EXPECTED_WEIGHTS:
        if expected not in found_weights:
            log.warning(
                "Expected weight %d not found in sources (present: %s). "
                "Skipping rather than fabricating a face.",
                expected,
                sorted(found_weights),
            )

    if not faces:
        raise BuildError("No faces built")

    if args.subset:
        subset_dir = dist_dir / "subsets"
        subset_dir.mkdir(parents=True, exist_ok=True)
        sample = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0-9 ,.!?;:'\"()[]{}<>"
        for info, meta in zip(faces, face_files):
            out = subset_dir / f"{info.output_name}-latin.woff2"
            convert_subset(info.path, out, sample)
            log.info("Subset  %s (%d bytes)", out.name, out.stat().st_size)

    if not args.skip_css:
        css_path = write_css(face_files, dist_dir / "style.css")
        log.info("Wrote %s", css_path)

    meta_path = write_metadata(dist_dir, faces, face_files, source_manifest=source_manifest)
    log.info("Wrote %s", meta_path)

    sha_path = write_sha256(dist_dir)
    log.info("Wrote %s", sha_path)

    # Keep OFL next to dist for Release convenience if present at repo root.
    ofl_src = Path(__file__).resolve().parent.parent / "OFL.txt"
    if ofl_src.is_file():
        shutil.copy2(ofl_src, dist_dir / "OFL.txt")
        # Refresh hash list to include OFL.txt
        write_sha256(dist_dir)

    if args.update_upstream_version and source_manifest and source_manifest.get("version"):
        write_upstream_version(str(source_manifest["version"]))
        log.info("Updated UPSTREAM_VERSION -> %s", source_manifest["version"])

    log.info("Build complete: %d WOFF2 face(s) in %s", len(faces), dist_dir)
    for meta in face_files:
        log.info("  %s  %d bytes  weight=%s", meta["file"], meta["size"], meta["weight"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
