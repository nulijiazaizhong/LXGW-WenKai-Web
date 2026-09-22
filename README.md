# lxgw-wenkai-webfont

Auto-updating **WOFF2 WebFont** packaging for [LXGW WenKai / 霞鹜文楷](https://github.com/lxgw/LxgwWenKai).

This repository is a WebFont packaging project for LXGW WenKai.

The original font is created by LXGW.

This project does not claim ownership of the original font and is **not** an official LXGW repository.

> Tracked upstream: [`lxgw/LxgwWenKai`](https://github.com/lxgw/LxgwWenKai) · License: **SIL OFL 1.1** ([OFL.txt](./OFL.txt))

---

## Features

- Discovers the **latest upstream Release** via the GitHub API (no hardcoded download URLs)
- Converts full TTF faces to **WOFF2** with [fontTools](https://github.com/fonttools/fonttools) (no glyph redesign)
- Generates `style.css`, `metadata.json`, and `sha256.txt`
- Daily upstream check → **PR-based** update flow (production CDN is not hot-swapped)
- Publishes assets on **GitHub Release**; serve them with **jsDelivr** using a **pinned version**
- Windows / Linux local builds; CI-ready scripts with clear fail-fast errors

Default product is **full WOFF2** (no aggressive CJK subsetting) so rare characters keep working.

---

## Upstream

| | |
| --- | --- |
| Repository | <https://github.com/lxgw/LxgwWenKai> |
| Font | LXGW WenKai / 霞鹜文楷 |
| License | SIL Open Font License 1.1 (with additional webfont packaging permission — see `OFL.txt`) |
| Last packaged upstream version | see [`UPSTREAM_VERSION`](./UPSTREAM_VERSION) |

Fonts are **not** committed to this repository. They are downloaded into `build/source/` at build time and packaged into `dist/`.

---

## Repository layout

```text
lxgw-wenkai-webfont/
├── .github/workflows/
│   ├── build.yml          # download → build → test → artifact
│   ├── upstream-check.yml # daily check → validate → open PR
│   └── release.yml        # tag → build → GitHub Release
├── scripts/
│   ├── common.py
│   ├── check_upstream.py
│   ├── download.py
│   ├── build.py
│   ├── generate_css.py
│   └── test.py
├── src/                   # optional local sources (not committed)
├── build/source/          # downloaded TTF/OTF (gitignored)
├── dist/                  # WebFont package output (released)
├── LICENSE                # MIT for packaging code
├── OFL.txt                # font license (verbatim from upstream)
├── UPSTREAM_VERSION       # last packaged upstream version string
├── pyproject.toml
└── requirements.txt
```

---

## Local build

Requirements: **Python 3.11+** (Windows / Linux / macOS).

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt

# 1) Discover latest upstream Release and download TTF faces
python scripts/download.py

# 2) Convert to WOFF2 + CSS + metadata + sha256
python scripts/build.py

# 3) Validate artifacts
python scripts/test.py
```

Outputs land in `dist/`:

```text
dist/
├── LXGWWenKai-Light.woff2
├── LXGWWenKai-Regular.woff2
├── LXGWWenKai-Medium.woff2
├── style.css
├── metadata.json
├── sha256.txt
└── OFL.txt
```

### Useful flags

```bash
python scripts/download.py --include-mono          # also package Mono faces
python scripts/download.py --only Regular           # filter asset names
python scripts/download.py --force                  # re-download
python scripts/download.py --update-upstream-version

python scripts/build.py --subset                    # EXPERIMENTAL extra latin subsets
python scripts/test.py --allow-missing-weights
python scripts/check_upstream.py --json
```

### Versioning

Two independent layers (SemVer for the package):

| Version | Meaning | Where |
| --- | --- | --- |
| **WebFont version** | packaging / CSS / layout changes | `pyproject.toml` → `project.version` |
| **Upstream font version** | LXGW WenKai release (`1.522`, …) | `UPSTREAM_VERSION` + `metadata.json` |

- Rebuild only → patch (`1.0.1`)
- Build pipeline change → minor (`1.1.0`)
- Breaking CDN/layout change → major (`2.0.0`)

Upstream versions are treated as **opaque strings** (never `float()`); tags like `v1.522` are normalized to `1.522`.

---

## Auto-update mechanism

```text
lxgw/LxgwWenKai release
        ↓
upstream-check.yml (cron 03:00 UTC / manual)
        ↓
scripts/check_upstream.py
        ↓
no change → stop
        ↓
build + test
        ↓
PR: bump UPSTREAM_VERSION
        ↓
human review / merge
        ↓
tag vX.Y.Z → release.yml
        ↓
GitHub Release assets
        ↓
jsDelivr (pinned @vX.Y.Z)
```

Why PR instead of auto-publish? A broken or unexpected upstream asset must not silently replace production CDN files.

### Permissions

Workflows use the default `GITHUB_TOKEN` only (no personal PAT):

| Workflow | Permissions |
| --- | --- |
| `build.yml` | `contents: read` |
| `upstream-check.yml` | `contents: write`, `pull-requests: write` (create update PR) |
| `release.yml` | `contents: write` (create Release + upload assets) |

If your org blocks the default token from opening PRs, enable **“Allow GitHub Actions to create and approve pull requests”** in *Settings → Actions → General*, or create the PR manually from the check branch.

---

## GitHub Actions

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| `build.yml` | push/PR touching scripts, manual | Download → WOFF2 → test → artifact |
| `upstream-check.yml` | daily cron + manual | Compare `UPSTREAM_VERSION`, open PR on update |
| `release.yml` | tag `v*` / manual | Build and publish GitHub Release |

Fail-fast: download failures, missing/corrupt fonts, conversion errors, empty glyph sets, CSS/metadata/SHA problems all **fail the job**.

Pip is cached; **font binaries are never cached** between runs (each build re-downloads or uses this job’s `build/source/`).

---

## Release

1. Merge the upstream-update PR (if any).
2. Run **Actions → Release → Run workflow** and set `version` (e.g. `v1.0.0`).
3. The workflow builds, tests, **commits `dist/` into the release commit**, tags it, and publishes a GitHub Release with:

```text
*.woff2  style.css  metadata.json  sha256.txt  OFL.txt
```

Because `dist/` is part of the tagged tree, jsDelivr `/gh/` URLs resolve correctly.

Manual alternative:

```bash
python scripts/download.py --force --update-upstream-version
python scripts/build.py && python scripts/test.py
git add -f dist UPSTREAM_VERSION
git commit -m "release: v1.0.0 webfont package"
git tag v1.0.0
git push origin main v1.0.0
# then run Release workflow or gh release create
```

Release notes include WebFont version, upstream version, build date, and commit.

---

## jsDelivr usage

jsDelivr reads **this repository’s tags** — nothing is uploaded to jsDelivr.

This repository: [`nulijiazaizhong/LXGW-WenKai-Web`](https://github.com/nulijiazaizhong/LXGW-WenKai-Web).

### Production (pinned — recommended)

```html
<link
  rel="stylesheet"
  href="https://cdn.jsdelivr.net/gh/nulijiazaizhong/LXGW-WenKai-Web@v1.0.0/dist/style.css"
/>
```

```css
body {
  font-family: "LXGW WenKai", "LXGW WenKai TC", "PingFang SC", "Microsoft YaHei", sans-serif;
}
```

Direct WOFF2 (same pin):

```text
https://cdn.jsdelivr.net/gh/nulijiazaizhong/LXGW-WenKai-Web@v1.0.0/dist/LXGWWenKai-Regular.woff2
https://cdn.jsdelivr.net/gh/nulijiazaizhong/LXGW-WenKai-Web@v1.0.0/dist/LXGWWenKai-Medium.woff2
https://cdn.jsdelivr.net/gh/nulijiazaizhong/LXGW-WenKai-Web@v1.0.0/dist/LXGWWenKai-Light.woff2
```

### Version selectors

| Selector | Use |
| --- | --- |
| `@v1.0.0` | **Production** — immutable, cache-friendly |
| `@v1.0` / `@1` | jsDelivr semver ranges (floating within major/minor); pin if you need strict immutability |
| `@main` | **Testing only** — changes without notice; not for production |
| `@latest` | **Not recommended** for production |

Generated CSS uses **relative** `url("./….woff2")`, so the same `style.css` works from GitHub raw, jsDelivr, or your own host.

### Self-host

Download a Release, serve `dist/` over HTTPS, and keep `style.css` next to the `.woff2` files.

---

## npm (optional)

Core builds do **not** depend on npm. If you want an npm package later:

1. Choose an available package name (scoped `@your-org/lxgw-wenkai-webfont` is safest).
2. Publish `dist/`, `OFL.txt`, and `README.md` only.
3. Keep `package.json` `name` aligned with the npm scope you actually publish.

---

## Security / design notes

- Least privilege: `contents: read` unless writing a PR or Release
- No secrets committed; `GITHUB_TOKEN` / `GH_TOKEN` is optional locally (raises API rate limits)
- No personal PAT required
- No font redesign: name tables and glyphs are not rewritten; only WOFF2 re-encode
- Full-font default: subsets are opt-in and never replace full faces

---

## What you must change before publishing

1. Repo coordinates are set to `nulijiazaizhong/LXGW-WenKai-Web` (README, `release.yml`, `pyproject.toml`, User-Agent).
2. Adjust `pyproject.toml` `version` when you cut a new WebFont SemVer.
3. Enable Actions + PR creation permissions (see above) if needed.
4. `UPSTREAM_VERSION` tracks the last packaged upstream release for `check_upstream` diffs.

---

## First publish checklist

```bash
git init   # if needed
git add .
git commit -m "feat: LXGW WenKai WOFF2 webfont packaging"
git branch -M main
git remote add origin https://github.com/nulijiazaizhong/LXGW-WenKai-Web.git
git push -u origin main

# Prefer the Release workflow (builds dist into the tag):
#   Actions → Release → Run workflow → version=v1.0.0
```

Then open `https://cdn.jsdelivr.net/gh/nulijiazaizhong/LXGW-WenKai-Web@v1.0.0/dist/style.css` and confirm fonts load.

---

## License

- **Packaging code / docs** in this repository: [MIT](./LICENSE)
- **Font software (LXGW WenKai) and WOFF2 derivatives**: [SIL Open Font License 1.1](./OFL.txt)

  Upstream copyright: LXGW and the Klee Project Authors. Reserved Font Names apply; additional permission allows format conversion (WOFF/WOFF2) for **webfont delivery** — see `OFL.txt`. Do not republish as installable desktop fonts under the reserved names.

- Original project: <https://github.com/lxgw/LxgwWenKai>
