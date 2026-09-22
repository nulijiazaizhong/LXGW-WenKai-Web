#!/usr/bin/env python3
"""Check whether upstream LXGW WenKai has a newer release than UPSTREAM_VERSION.

Exit codes:
  0 — up to date (or check completed without requiring a rebuild)
  10 — new upstream version detected (Actions should proceed to update/build)
  1  — failure (network, API, unexpected payload)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.common import (  # type: ignore[import-not-found]
        BuildError,
        UpstreamError,
        fetch_latest_release,
        normalize_version,
        read_upstream_version,
        setup_logging,
        versions_equal,
    )
else:
    from .common import (
        BuildError,
        UpstreamError,
        fetch_latest_release,
        normalize_version,
        read_upstream_version,
        setup_logging,
        versions_equal,
    )

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UPDATE = 10


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable summary on stdout",
    )
    parser.add_argument(
        "--fail-on-update",
        action="store_true",
        help="Exit 10 when an update is available (default behavior for CI)",
    )
    parser.add_argument(
        "--write-upstream-version",
        action="store_true",
        help="On update, rewrite UPSTREAM_VERSION to the latest tag version",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON summary (for Actions artifacts)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = setup_logging(args.verbose)

    try:
        release = fetch_latest_release()
    except (UpstreamError, BuildError) as exc:
        log.error("Upstream check failed: %s", exc)
        return EXIT_ERROR

    local_raw = read_upstream_version()
    local = normalize_version(local_raw) if local_raw else None
    remote = release.version

    has_update = not versions_equal(local, remote)
    # Also treat "empty local" as update (first bootstrap).
    if local is None:
        has_update = True

    summary = {
        "has_update": has_update,
        "local_version": local,
        "upstream_version": remote,
        "upstream_tag": release.tag_name,
        "upstream_commit": release.commit_sha,
        "upstream_url": release.html_url,
        "published_at": release.published_at,
        "assets": [
            {"name": a.name, "size": a.size, "url": a.browser_download_url} for a in release.assets
        ],
    }

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    if not has_update:
        log.info("Upstream is already up to date.")
        log.info("  local    = %s", local or "(none)")
        log.info("  upstream = %s (tag %s)", remote, release.tag_name)
        return EXIT_OK

    log.info("New upstream version detected:")
    log.info("  old = %s", local or "(none)")
    log.info("  new = %s", remote)
    log.info("  tag = %s", release.tag_name)
    log.info("  commit = %s", release.commit_sha or "(unknown)")
    log.info("  url = %s", release.html_url)

    if args.write_upstream_version:
        if __package__ in (None, ""):
            from scripts.common import write_upstream_version  # type: ignore[import-not-found]
        else:
            from .common import write_upstream_version

        write_upstream_version(remote)
        log.info("Wrote UPSTREAM_VERSION = %s", remote)

    # GitHub Actions step summary
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write("## Upstream check\n\n")
            fh.write(f"- Local: `{local or '(none)'}`\n")
            fh.write(f"- Upstream: `{remote}` (`{release.tag_name}`)\n")
            fh.write(f"- Commit: `{release.commit_sha or 'unknown'}`\n")
            fh.write(f"- URL: {release.html_url}\n")
            if has_update:
                fh.write("\n**Update available.**\n")

    if args.fail_on_update or True:
        # Default: non-zero distinct code so CI can branch on update.
        return EXIT_UPDATE if has_update else EXIT_OK
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
