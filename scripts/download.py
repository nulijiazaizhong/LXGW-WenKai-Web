#!/usr/bin/env python3
"""Download upstream LXGW WenKai font assets into build/source/.

Discovers the latest GitHub Release (no hardcoded version URLs), selects
static TTF/OTF faces (prefer TTF), downloads them, and records a source
manifest for the build step.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.common import (  # type: ignore[import-not-found]
        SOURCE_DIR,
        UPSTREAM_REPO,
        BuildError,
        ReleaseAsset,
        UpstreamError,
        ensure_dirs,
        fetch_latest_release,
        github_token,
        http_download,
        is_font_asset,
        is_mono_filename,
        parse_weight_from_filename,
        pick_font_assets,
        setup_logging,
        sha256_file,
        write_upstream_version,
    )
else:
    from .common import (
        SOURCE_DIR,
        UPSTREAM_REPO,
        BuildError,
        ReleaseAsset,
        UpstreamError,
        ensure_dirs,
        fetch_latest_release,
        github_token,
        http_download,
        is_font_asset,
        is_mono_filename,
        parse_weight_from_filename,
        pick_font_assets,
        setup_logging,
        sha256_file,
        write_upstream_version,
    )

SOURCE_MANIFEST = SOURCE_DIR / "source_manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--include-mono",
        action="store_true",
        help="Also package LXGW WenKai Mono faces (default: standard family only)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="NAME",
        help="Only download assets whose filename contains NAME (repeatable)",
    )
    parser.add_argument(
        "--update-upstream-version",
        action="store_true",
        help="Rewrite UPSTREAM_VERSION after a successful download",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a file with the same name already exists",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = setup_logging(args.verbose)
    ensure_dirs()
    token = github_token()

    log.info("Querying %s latest release…", UPSTREAM_REPO)
    try:
        release = fetch_latest_release(token=token)
    except (UpstreamError, BuildError) as exc:
        log.error("Failed to discover upstream release: %s", exc)
        return 1

    log.info("Latest release: tag=%s version=%s name=%s", release.tag_name, release.version, release.name)
    log.info("  url    = %s", release.html_url)
    log.info("  commit = %s", release.commit_sha or "(unknown)")
    log.info("  published_at = %s", release.published_at)
    log.info("  assets = %d", len(release.assets))
    for asset in release.assets:
        log.debug("    - %s (%d bytes)", asset.name, asset.size)

    font_assets_all = [a for a in release.assets if is_font_asset(a.name)]
    if not font_assets_all:
        log.error(
            "Release %s has no .ttf/.otf assets. Assets present: %s",
            release.tag_name,
            [a.name for a in release.assets],
        )
        return 1

    selected = pick_font_assets(release.assets, include_mono=args.include_mono)
    if args.only:
        needles = [n.lower() for n in args.only]
        selected = [a for a in selected if any(n in a.name.lower() for n in needles)]

    if not selected:
        log.error(
            "No font assets selected after filtering. "
            "All font assets: %s",
            [a.name for a in font_assets_all],
        )
        return 1

    log.info("Selected %d font asset(s):", len(selected))
    for asset in selected:
        weight = parse_weight_from_filename(asset.name)
        mono = " mono" if is_mono_filename(asset.name) else ""
        log.info("  - %s (weight≈%s)%s  %d bytes", asset.name, weight, mono, asset.size)

    downloaded: list[dict[str, object]] = []
    for asset in selected:
        dest = SOURCE_DIR / asset.name
        if dest.is_file() and dest.stat().st_size > 0 and not args.force:
            log.info("Reusing cached %s", dest.name)
        else:
            url = asset.browser_download_url or asset.url
            if not url:
                raise UpstreamError(f"Asset {asset.name} has no download URL")
            log.info("Downloading %s …", asset.name)
            try:
                http_download(url, dest, token=token)
            except UpstreamError as exc:
                log.error("Download failed for %s: %s", asset.name, exc)
                return 1

        if not dest.is_file() or dest.stat().st_size == 0:
            log.error("Downloaded file missing or empty: %s", dest)
            return 1

        # Basic sanity: TTF/OTF starts with known sfnt tags / wOFF is wrong here.
        head = dest.read_bytes()[:4]
        if head not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
            log.error(
                "%s does not look like TTF/OTF (magic=%r). File may be corrupt or HTML error page.",
                dest.name,
                head,
            )
            return 1

        digest = sha256_file(dest)
        downloaded.append(
            {
                "name": dest.name,
                "path": str(dest.relative_to(SOURCE_DIR.parent.parent)).replace("\\", "/"),
                "size": dest.stat().st_size,
                "sha256": digest,
                "download_url": asset.browser_download_url or asset.url,
                "api_url": asset.url,
                "release_digest": asset.digest,
            }
        )
        log.info("  saved %s  sha256=%s", dest.name, digest)

    manifest = {
        "upstream_repo": UPSTREAM_REPO,
        "tag_name": release.tag_name,
        "version": release.version,
        "release_name": release.name,
        "release_url": release.html_url,
        "commit_sha": release.commit_sha,
        "published_at": release.published_at,
        "include_mono": bool(args.include_mono),
        "files": downloaded,
    }
    SOURCE_MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("Wrote %s", SOURCE_MANIFEST)

    if args.update_upstream_version:
        write_upstream_version(release.version)
        log.info("Updated UPSTREAM_VERSION -> %s", release.version)

    log.info("Download complete: %d file(s) in %s", len(downloaded), SOURCE_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
