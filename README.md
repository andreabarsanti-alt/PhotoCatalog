# PhotoCatalog  v0.0.10

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
.venv/bin/pip install osxphotos imagehash Pillow pillow-heif exifread tqdm

# Launch the GUI
.venv/bin/python3 photocatalog_gui.py
```

> No external tools required — folder ingestion uses the pure-Python `exifread` library.

---

## GUI quick-start

The GUI has four tabs:

| Tab | What it does |
|---|---|
| **Build Catalog** | Ingest from Mac Photos, Lightroom, or a folder |
| **Find Duplicates** | Run duplicate-detection and store results |
| **Browse Duplicates** | Launch a local web UI to review groups and mark Keep / Discard |
| **About** | Version info and in-app update checker |

### Typical workflow

1. **Build Catalog** — add all your photo sources (repeat for each source; rows are never
   duplicated across runs; safe to interrupt and resume)
2. **Find Duplicates** — runs the unified strategy automatically
3. **Browse Duplicates** → review each group, mark one copy as Keep and the rest as Discard
4. *(coming soon)* Resolve — safe bulk-move of discarded copies

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
#   --workers N        Parallel threads for hashing (0 = auto, default: min(cpu_count, 8))

# Find duplicates
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db               # unified (recommended)
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy phash
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy metadata
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy filename

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

---

## Duplicate browser — filter and selection actions

The left pane has **signal filter badges** — click one or more to narrow the list to groups
where those signals agree:

| Badge | Meaning |
|---|---|
| pHash | All photos share the same perceptual hash |
| Dims | Same rotation-aware width/height |
| Date | Same capture date |
| Type | Same file type |
| Size | Same file size |
| Name | Common filename stem |
| Unique | Show groups already reduced to a single survivor |
| Video | Show only groups containing at least one video file |

The top bar provides **bulk actions** scoped to the currently visible groups:

| Action | Effect |
|---|---|
| Reset selection | Clear all Keep / Discard decisions |
| Keep unique | In groups where exactly one photo is not discarded, mark it Keep |
| Prefer filename… | Discard photos whose filename doesn't match a Python regex |
| Prefer path… | Same, matched against the full file path |
| Prefer type… | Same, matched against file type (e.g. `DNG`, `JPEG`) |
| Prefer higher resolution | Discard photos below the max(width, height) in the group |
| Prefer bigger | Discard photos below the largest file size in the group |

---

## Resumable operations

All long-running operations are safe to interrupt (Ctrl-C) and resume:

- **Folder ingestion** — already-ingested files are detected from the DB and skipped entirely;
  the progress bar shows 0→100% of the remaining work only
- **Find duplicates** — scoring results are checkpointed after computation; a re-run reloads
  the checkpoint and skips straight to applying results if the catalog hasn't changed

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
```

---

## Roadmap

- [x] Unified catalog (Mac Photos + Lightroom + folders)
- [x] Perceptual hash + metadata + filename duplicate detection
- [x] Web UI to browse and mark duplicates
- [x] Bulk selection actions (prefer filename/path/type/resolution/size, keep unique)
- [x] Video filter in duplicate browser
- [x] GUI launcher with in-app updater
- [x] Self-contained app bundle (no external tools required)
- [x] Resumable ingestion and duplicate-finding
- [x] Parallel perceptual hashing (multi-core, configurable)
- [ ] Resolve — bulk-move discarded copies to a holding folder
- [ ] Multi-machine export — portable DB or sync mechanism
