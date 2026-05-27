# PhotoCatalog  v0.0.1

**Find and safely remove duplicate photos across Mac Photos, Lightroom Classic, and disk folders.**

Builds a unified SQLite catalog from all your photo sources, identifies duplicates with multiple
detection strategies (perceptual hash, metadata fingerprint, filename + date), and lets you review
them in a web-based UI where you can mark each copy as Keep or Discard.

---

## Install

### Option A — Download the app (recommended)

1. Go to the [latest release](https://github.com/andreabarsanti-alt/PhotoCatalog/releases/latest)
2. Download `PhotoCatalog-x.x.x.dmg`
3. Open the DMG and drag **PhotoCatalog.app** to `/Applications`
4. Launch from `/Applications` — the app will check for updates automatically on every launch

### Option B — Run from source (development)

```bash
git clone git@github.com:andreabarsanti-alt/PhotoCatalog.git
cd PhotoCatalog
python3 -m venv .venv
.venv/bin/pip install osxphotos imagehash Pillow pillow-heif
brew install exiftool          # needed for Folder source ingestion

# Launch the GUI
.venv/bin/python3 photocatalog_gui.py
# Or double-click PhotoCatalog.command in Finder
```

---

## GUI quick-start

The GUI has five tabs:

| Tab | What it does |
|---|---|
| **Build Catalog** | Ingest from Mac Photos, Lightroom, or a folder |
| **Find Duplicates** | Run duplicate-detection strategies and store results |
| **Browse Duplicates** | Launch a local web UI to review groups and mark Keep / Discard |
| **Examples** | Pre-filled example workflows — click Load then switch tabs |
| **About** | Version info and in-app update checker |

### Typical workflow

1. **Build Catalog** — add all your photo sources (repeat for each source; rows are never
   duplicated across runs)
2. **Find Duplicates** → unified strategy, min-score 2
3. **Browse Duplicates** → review each group, mark one copy as Keep and the rest as Discard
4. *(future)* Resolve — safe bulk-move of discarded copies

---

## Command-line usage

All commands use the project venv:

```bash
# Ingest sources
.venv/bin/python3 -m PhotoCatalog.build_catalog --source MacPhotos --db catalog.db
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Lightroom  --db catalog.db --path /path/to/Catalog.lrcat
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Folder     --db catalog.db --path /Volumes/DISK/Photos

# Options:
#   --fresh            Drop and recreate the DB before ingesting
#   --download         (MacPhotos only) Download iCloud-only photos locally
#   --download-limit N Cap iCloud downloads per run

# Find duplicates
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db                  # unified (recommended)
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --min-score 4    # stricter
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy all   # all strategies

# Browse web UI
.venv/bin/python3 -m PhotoCatalog.serve_duplicates --db catalog.db
```

---

## Duplicate-detection strategies

| Strategy | Logic | Notes |
|---|---|---|
| `unified` *(default)* | Union-find across all three signals, then score | Recommended — catches the most true duplicates |
| `phash` | Identical perceptual hash | Most precise; requires hashes (computed automatically at ingest) |
| `metadata` | Same dimensions + date + file type | Rotation-aware; fast |
| `filename` | Same filename stem + same capture minute | Handles Mac Photos UUID renaming |

Unified **score** = `(10 + n_other)` if phash matches, else `n_other` (0–5 other signals).
Default `--min-score 2` keeps groups that agree on at least two signals.  Group #1 = strongest.

---

## Database schema

Catalogs live in `~/Pictures/PhotoCatalogs/` by default.  Each `.db` file is a standard SQLite
database with two tables and one view:

- **`photos`** — one row per file (60 columns: path, EXIF, dimensions, dates, camera, perceptual hash, …)
- **`duplicate_groups`** — one row per (strategy, photo) pair; re-running a strategy clears only its rows
- **`photos_with_groups`** view — convenience join showing all three group IDs per photo

---

## Building and releasing (for maintainers)

Requirements: `pip install pyinstaller`, `brew install create-dmg gh`

```bash
# Build the .app
make build

# Build the .app + package into a DMG
make dmg

# Publish to GitHub Releases (tags, uploads DMG, generates release notes)
make release

# Bump version, then release
make bump VERSION=0.0.2
git add PhotoCatalog/__init__.py pyproject.toml
git commit -m "Bump version to 0.0.2"
make release
```

---

## Roadmap

- [x] Unified catalog (Mac Photos + Lightroom + folders)
- [x] Perceptual hash + metadata + filename duplicate detection
- [x] Web UI to browse and mark duplicates
- [x] GUI launcher with in-app updater
- [ ] Resolve — bulk-move discarded copies to a holding folder
- [ ] Multi-machine export — portable DB or sync mechanism
