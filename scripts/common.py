"""Shared helpers for LXGW WenKai WebFont packaging scripts."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project paths (always relative to repository root)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
BUILD_DIR = ROOT / "build"
SOURCE_DIR = BUILD_DIR / "source"
DIST_DIR = ROOT / "dist"
UPSTREAM_VERSION_FILE = ROOT / "UPSTREAM_VERSION"

UPSTREAM_REPO = "lxgw/LxgwWenKai"
UPSTREAM_API = f"https://api.github.com/repos/{UPSTREAM_REPO}"
UPSTREAM_WEB = f"https://github.com/{UPSTREAM_REPO}"
FONT_DISPLAY_NAME = "LXGW WenKai"
FONT_FAMILY_CSS = "LXGW WenKai"
LICENSE_ID = "OFL-1.1"

# Canonical styles we try to package in phase 1 (non-Mono).
# Missing styles are skipped with a clear warning; required set is checked later.
WEIGHT_TOKENS: dict[str, int] = {
    "thin": 100,
    "extralight": 200,
    "ultralight": 200,
    "light": 300,
    "regular": 400,
    "normal": 400,
    "book": 400,
    "medium": 500,
    "semibold": 600,
    "demibold": 600,
    "bold": 700,
    "extrabold": 800,
    "ultrabold": 800,
    "black": 900,
    "heavy": 900,
}

# Phase-1 expected weights for the default family (warn if missing, fail if empty).
PHASE1_EXPECTED_WEIGHTS: tuple[int, ...] = (300, 400, 500)

USER_AGENT = "lxgw-wenkai-webfont-builder/1.0 (+https://github.com/nulijiazaizhong/LXGW-WenKai-Web)"


class BuildError(Exception):
    """Fatal packaging error — callers must fail fast."""


class UpstreamError(BuildError):
    """Upstream discovery / download failure."""


def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    return logging.getLogger("lxgw-wenkai-webfont")


def ensure_dirs() -> None:
    for path in (SRC_DIR, BUILD_DIR, SOURCE_DIR, DIST_DIR):
        path.mkdir(parents=True, exist_ok=True)


def normalize_version(raw: str | None) -> str | None:
    """Normalize upstream version strings without assuming numeric format.

    Examples: 'v1.522' -> '1.522', '1.7' stays '1.7', '1.7.0-beta' stays.
    Never cast to float/int for comparison beyond string equality after normalize.
    """
    if raw is None:
        return None
    version = raw.strip()
    if not version:
        return None
    # Strip a single leading v/V only when followed by a digit.
    if len(version) > 1 and version[0] in "vV" and version[1].isdigit():
        version = version[1:]
    return version


def versions_equal(a: str | None, b: str | None) -> bool:
    na = normalize_version(a)
    nb = normalize_version(b)
    if na is None or nb is None:
        return False
    return na == nb


def read_upstream_version() -> str | None:
    if not UPSTREAM_VERSION_FILE.is_file():
        return None
    text = UPSTREAM_VERSION_FILE.read_text(encoding="utf-8").strip()
    return text or None


def write_upstream_version(version: str) -> None:
    normalized = normalize_version(version)
    if not normalized:
        raise BuildError(f"Refusing to write empty UPSTREAM_VERSION (raw={version!r})")
    UPSTREAM_VERSION_FILE.write_text(normalized + "\n", encoding="utf-8")


def read_webfont_version() -> str:
    """WebFont package SemVer lives in pyproject.toml (independent of upstream)."""
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.is_file():
        raise BuildError("pyproject.toml missing; cannot resolve WebFont version")
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise BuildError("pyproject.toml has no project.version field")
    return match.group(1)


def http_get_json(url: str, *, timeout: int = 30, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(body.decode(charset))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code == 403 and "rate limit" in detail.lower():
            raise UpstreamError(
                f"GitHub API rate limit hit for {url}. "
                "Set GITHUB_TOKEN or wait for the limit to reset."
            ) from exc
        raise UpstreamError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise UpstreamError(f"Network error for {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise UpstreamError(f"Invalid JSON from {url}: {exc}") from exc


def http_download(url: str, dest: Path, *, timeout: int = 300, token: str | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, tmp.open("wb") as out:
            total = response.headers.get("Content-Length")
            total_bytes = int(total) if total and total.isdigit() else None
            copied = 0
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
                copied += len(chunk)
                if total_bytes and copied % (1024 * 1024 * 5) < 256 * 1024:
                    pct = 100.0 * copied / total_bytes
                    logging.getLogger("download").info(
                        "  %s: %.1f%% (%.1f MB / %.1f MB)",
                        dest.name,
                        pct,
                        copied / 1e6,
                        total_bytes / 1e6,
                    )
        if dest.exists():
            dest.unlink()
        tmp.replace(dest)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        tmp.unlink(missing_ok=True)
        raise UpstreamError(f"Download HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise UpstreamError(f"Download network error for {url}: {exc.reason}") from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    url: str
    browser_download_url: str
    content_type: str
    digest: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ReleaseAsset:
        return cls(
            name=str(data["name"]),
            size=int(data.get("size") or 0),
            url=str(data["url"]),
            browser_download_url=str(data.get("browser_download_url") or ""),
            content_type=str(data.get("content_type") or ""),
            digest=data.get("digest"),
        )


@dataclass(frozen=True)
class UpstreamRelease:
    tag_name: str
    version: str
    name: str
    html_url: str
    published_at: str | None
    target_commitish: str
    commit_sha: str | None
    assets: list[ReleaseAsset] = field(default_factory=list)
    prerelease: bool = False
    draft: bool = False


def fetch_latest_release(token: str | None = None) -> UpstreamRelease:
    data = http_get_json(f"{UPSTREAM_API}/releases/latest", token=token or github_token())
    if not isinstance(data, dict) or "tag_name" not in data:
        raise UpstreamError(f"Unexpected release payload for {UPSTREAM_REPO}")

    tag_name = str(data["tag_name"])
    version = normalize_version(tag_name) or tag_name
    commit_sha = _resolve_tag_commit(tag_name, token=token or github_token())
    assets = [ReleaseAsset.from_api(a) for a in data.get("assets") or []]
    return UpstreamRelease(
        tag_name=tag_name,
        version=version,
        name=str(data.get("name") or tag_name),
        html_url=str(data.get("html_url") or ""),
        published_at=data.get("published_at"),
        target_commitish=str(data.get("target_commitish") or ""),
        commit_sha=commit_sha,
        assets=assets,
        prerelease=bool(data.get("prerelease")),
        draft=bool(data.get("draft")),
    )


def _resolve_tag_commit(tag_name: str, *, token: str | None) -> str | None:
    """Resolve tag -> commit SHA. Prefer annotated tag peel, fall back to tag object."""
    try:
        ref = http_get_json(f"{UPSTREAM_API}/git/ref/tags/{tag_name}", token=token)
        obj = ref.get("object") or {}
        sha = obj.get("sha")
        obj_type = obj.get("type")
        if obj_type == "commit" and sha:
            return str(sha)
        if obj_type == "tag" and sha:
            tag_obj = http_get_json(f"{UPSTREAM_API}/git/tags/{sha}", token=token)
            target = (tag_obj.get("object") or {}).get("sha")
            return str(target) if target else None
    except UpstreamError:
        # Not fatal for packaging — metadata can still record the tag.
        logging.getLogger("upstream").warning("Could not resolve commit for tag %s", tag_name)
    return None


# ---------------------------------------------------------------------------
# Font identity helpers (used after download)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FontFaceInfo:
    path: Path
    family: str
    subfamily: str
    full_name: str
    weight_class: int
    is_italic: bool
    is_mono: bool
    glyph_count: int
    output_name: str  # e.g. LXGWWenKai-Regular.woff2 stem

    @property
    def style(self) -> str:
        return "italic" if self.is_italic else "normal"

    @property
    def css_weight(self) -> int:
        return self.weight_class


def parse_weight_from_filename(name: str) -> int | None:
    stem = Path(name).stem.lower()
    # Strip family prefixes we know about.
    for prefix in ("lxgwwenkaimono-", "lxgwwenkai-", "lxgwwenkai"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    stem = stem.strip("-_")
    if not stem:
        return None
    # Exact token match first.
    if stem in WEIGHT_TOKENS:
        return WEIGHT_TOKENS[stem]
    # e.g. "regular", "medium", "light", "semibold"
    for token, weight in sorted(WEIGHT_TOKENS.items(), key=lambda kv: -len(kv[0])):
        if token in stem:
            return weight
    return None


def is_mono_filename(name: str) -> bool:
    return "mono" in Path(name).stem.lower()


def is_font_asset(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".ttf") or lower.endswith(".otf")


def pick_font_assets(assets: list[ReleaseAsset], *, include_mono: bool = False) -> list[ReleaseAsset]:
    """Select installable font assets from a GitHub release.

    Rules (explicit and logged by the caller):
    1. Only .ttf / .otf (not zip/tar).
    2. Prefer TTF over OTF when both exist for the same weight.
    3. Exclude Mono family unless include_mono is True.
    4. Prefer non-variable filenames (no 'VF' / 'Variable' / '[' patterns).
    """
    fonts = [a for a in assets if is_font_asset(a.name)]
    if not include_mono:
        fonts = [a for a in fonts if not is_mono_filename(a.name)]
    # Drop obvious variable fonts for phase-1 static packaging.
    static = []
    for asset in fonts:
        lower = asset.name.lower()
        if any(tok in lower for tok in ("-vf", "_vf", "variable", "[wght]", "-wght")):
            continue
        static.append(asset)

    def preference_key(asset: ReleaseAsset) -> tuple[int, int, str]:
        ext_rank = 0 if asset.name.lower().endswith(".ttf") else 1
        weight = parse_weight_from_filename(asset.name) or 999
        return (weight, ext_rank, asset.name.lower())

    # Deduplicate by weight+style preferring TTF.
    chosen: dict[tuple[int, str], ReleaseAsset] = {}
    for asset in sorted(static, key=preference_key):
        weight = parse_weight_from_filename(asset.name) or 0
        key = (weight, "italic" if "italic" in asset.name.lower() else "normal")
        if key not in chosen:
            chosen[key] = asset

    selected = list(chosen.values())
    selected.sort(key=lambda a: (parse_weight_from_filename(a.name) or 0, a.name.lower()))
    return selected


def output_face_name(family_stem: str, weight: int, style: str) -> str:
    weight_label = {
        100: "Thin",
        200: "ExtraLight",
        300: "Light",
        400: "Regular",
        500: "Medium",
        600: "SemiBold",
        700: "Bold",
        800: "ExtraBold",
        900: "Black",
    }.get(weight, str(weight))
    if style == "italic":
        return f"{family_stem}-{weight_label}Italic"
    return f"{family_stem}-{weight_label}"


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fail(message: str, code: int = 1) -> None:
    logging.getLogger("lxgw-wenkai-webfont").error(message)
    raise SystemExit(code)
