#!/usr/bin/env python3
"""Compute the next WebFont SemVer tag for auto-publish.

Rules (string-safe, never float()):
  --from-tag v1.2.3 --bump patch  -> 1.2.4
  --from-tag v1.2.3 --bump minor  -> 1.3.0
  --from-tag v1.2.3 --bump major  -> 2.0.0
  --set 1.4.0                     -> 1.4.0
Missing / non-semver tags start from 1.0.0.
"""

from __future__ import annotations

import argparse
import re
import sys

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def parse_version(raw: str) -> tuple[int, int, int] | None:
    match = SEMVER_RE.match((raw or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump(version: tuple[int, int, int], part: str) -> str:
    major, minor, patch = version
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-tag", default="v0.0.0", help="Current latest tag")
    parser.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        default="patch",
        help="Which segment to increment",
    )
    parser.add_argument("--set", dest="force", default=None, help="Force this version")
    parser.add_argument(
        "--with-prefix",
        action="store_true",
        help="Print with leading v",
    )
    args = parser.parse_args(argv)

    if args.force:
        parsed = parse_version(args.force)
        if not parsed:
            print(f"Invalid version: {args.force!r}", file=sys.stderr)
            return 1
        version = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
    else:
        current = parse_version(args.from_tag) or (0, 0, 0)
        version = bump(current, args.bump)

    print(f"v{version}" if args.with_prefix else version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
