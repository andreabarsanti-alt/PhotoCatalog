# PhotoCatalog — Claude context

## What this project is
**Primary goal: find and safely remove duplicate photos across multiple sources.**

Builds a unified SQLite catalog from macOS Photos, Lightroom Classic, and disk folders, identifies duplicates across all sources, and resolves them safely. Broader catalog management features (organisation, export, etc.) are out of scope for now.

## Repo layout
```
PhotoCatalog/           # Python package
    db.py                  # Schema, connect(), init_db(), insert_photos()
    build_catalog.py       # CLI: ingest from a source into the DB (hashing runs automatically)
    find_duplicates.py     # CLI: run duplicate-detection strategies
    explore_duplicates.py  # CLI: browse duplicate groups (terminal + HTML report)
    serve_duplicates.py    # Web UI: two-pane duplicate browser with image previews
    enrich.py              # Computed enrichment: perceptual hashes, iCloud download
    sources/
        mac_photos.py   # Ingest via osxphotos Python library
        lightroom.py    # Ingest via direct SQLite read of .lrcat
        folder.py       # Ingest via exifread (pure Python, no external tools)
photocatalog_gui.py     # Tkinter GUI launcher (also PyInstaller entry point)
```

## Python environment
Always use the project venv: `.venv/bin/python3` — never the system python3.

## Database schema (catalog.db)
Two tables, one view:

**`photos`** — one row per file, 60 columns including:
- Identity: `source_file` (UNIQUE), `source_type`, `source_catalog`, `file_name`, `original_filename`
  - `source_catalog`: path to the exact catalog/library/folder the photo came from
    - Lightroom → path to the `.lrcat` file
    - MacPhotos → path to `Photos.sqlite` inside the `.photoslibrary` bundle
    - Folder → root folder that was scanned
- File: `file_type`, `file_size`, `image_width`, `image_height`
- Dates (ISO 8601): `date_original`, `date_created`, `date_modified`
- Camera: `make`, `model`, `lens_model`, `focal_length`, `aperture`, `iso`, `shutter_speed`
- Location: `latitude`, `longitude`
- Computed: `perceptual_hash` (phash hash_size=16, 64 hex chars) — populated automatically at ingest
- Unified curation: `caption`, `keywords` (JSON array), `has_adjustments`
- MacPhotos-specific: `mp_uuid`, `mp_favorite`, `mp_score`, `mp_is_live`, `mp_is_burst`
- Lightroom-specific: `lr_rating`, `lr_pick`, `lr_color_labels`, and develop-setting columns

**`duplicate_groups`** — one row per (strategy, photo) pair:
- `group_id INTEGER`, `strategy TEXT` (always `'groups'`), `photo_id`, `match_info JSON`, `found_at`
- PRIMARY KEY (strategy, photo_id) — one group per photo per strategy
- Each run of `find_groups()` clears and rewrites all unresolved groups (resolved ones are optionally preserved)

**`decisions`** — one row per photo with a keep/discard decision:
- `photo_id INTEGER PRIMARY KEY`, `action TEXT` ('keep'|'discard'), `decided_at`

**`photos_with_groups` view** — joins photos with its group IDs as convenience columns (`groups_group_id`, `phash_group_id`, `meta_group_id`, `filename_group_id`)

## Key design decisions
- **SQLite not JSON**: the old codebase used JSON files; switching to SQLite enables incremental updates, indexed queries, and joins across sources
- **osxphotos Python library** (not CLI): used for Mac Photos ingestion — abstracts the Photos.sqlite schema which changes between macOS versions
- **exifread not exiftool**: folder ingestion uses pure-Python `exifread`; no external binary dependency
- **original_filename vs file_name**: MacPhotos renames files to UUIDs on disk; `file_name` is the UUID, `original_filename` is what was imported. Both are checked in filename duplicate strategy
- **Rotation-aware metadata matching**: uses MIN/MAX of width/height so portrait and landscape versions match
- **Two perceptual hash columns**: `perceptual_hash` (phash hash_size=16, 64 hex chars) for exact matching; `perceptual_hash_small` (phash hash_size=8, 16 hex chars) for fuzzy Hamming matching. Both are computed at ingest.
- **Hashing runs automatically at ingest**: `build_catalog.py` always calls `add_perceptual_hashes()` after ingestion; only files accessible on disk at that moment are hashed
- **Lightroom file_size**: `AgLibraryFileAssetMetadata` and `AgLibraryFileDigest` are empty in this catalog; sizes come from `AgParsedImportHash` (via `id_global`, ~37% coverage) then fall back to `os.stat()` for locally accessible files
- **insert_photos uses union of all row keys**: handles heterogeneous batches correctly
- **`find_groups()` replaces old strategies**: single entry point with 7 configurable signals (phash, fuzzy, dims, date, type, size, name), AND/OR logic, and optional `--keep-resolved`. Strategy column is always `'groups'`. Score = (10+n) if phash else n; all 7 signal booleans are stored in every `match_info` row so the web UI can filter freely.
- **`caffeinate -i`**: Build Catalog and Find Duplicates subprocesses are wrapped with `caffeinate -i` to prevent macOS idle sleep; Browse Duplicates is NOT (it runs indefinitely)
- **Subprocess window fix**: the GUI spawns `PhotoCatalogCLI` (a plain binary in `Contents/MacOS/`) instead of the main `PhotoCatalog` app binary. Because it is NOT the `CFBundleExecutable`, macOS does not go through app-launch infrastructure for it — no extra Dock icon or window appears
- **Parallel hashing**: `add_perceptual_hashes()` uses `ThreadPoolExecutor` with `min(os.cpu_count(), 8)` threads by default (auto, configurable via `--workers`); commits every 200 hashes instead of per-file

## Resumable operations
Folder ingestion is safe to interrupt and resume:

**Folder ingestion** (`sources/folder.py`):
- Before walking, queries `SELECT source_file FROM photos WHERE source_catalog = ?`
- Filters the file list to only unprocessed files — already-ingested files are skipped entirely (no EXIF extraction, no INSERT attempt)
- Progress bar shows 0→100% of remaining work; prints "Resuming: N already ingested, M remaining"

## find_groups() — signal-based duplicate detection (find_duplicates.py)
Eight signals, all computed for every group and stored in `match_info`:
- `phash` — exact perceptual hash match (hash_size=16)
- `fuzzy` — Hamming distance ≤ 10 on hash_size=8 hash (near-duplicates, light edits)
- `dims`  — rotation-aware (min, max) of width/height
- `date`  — exact date_original
- `type`  — file_type
- `size`  — file_size
- `name`  — common filename stem + date minute
- `blank` — all photos have a degenerate perceptual hash (< 10 bits set), indicating uniform/blank images

`score = (10 + n_others) if phash else n_others` — groups inserted in descending score order. `blank` does not contribute to score.

**Logic modes**:
- `OR`  — union-find: photos are connected if they match on ANY selected signal
- `AND` — compound bucketing: photos grouped only when ALL selected signals match; fuzzy applied as a secondary filter within each compound bucket

**`--keep-resolved`**: photos in groups that already have exactly one `keep` decision are excluded from the search; their groups are preserved as-is.

## Web UI filters (serve_duplicates.py)
Signal filter badges in the left pane narrow the group list in real time:
- `pHash`, `Fuzzy`, `Dims`, `Date`, `Type`, `Size`, `Name`, `Blank` — signal badges (from match_info; all 8 always present)
- `Unique` — show groups already reduced to a single survivor (resolved groups)
- `Video` — show only groups containing at least one video file (MOV, MP4, M4V, AVI, MKV…)

Groups are sorted by score descending (strongest first), then by group_id.
`has_video` is computed in `_all_groups()` via `MAX(CASE WHEN UPPER(file_type) IN (...) THEN 1 ELSE 0 END)`.

## How to run
```bash
# Always use the venv
.venv/bin/python3 -m PhotoCatalog.build_catalog --source MacPhotos --db catalog.db
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Lightroom --db catalog.db --path /path/to/catalog.lrcat
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Folder    --db catalog.db --path /Volumes/DISK/Photos
# Hashing (both exact + fuzzy) runs automatically after each ingest.
# Safe to interrupt and re-run — already-ingested files are skipped.

# Find duplicates — default: phash signal, OR logic
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db
# Custom signals and logic:
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --signal phash fuzzy --logic OR
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --signal phash dims date --logic AND
# Preserve already-resolved groups, re-run only on the rest:
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --keep-resolved

# Browse duplicate groups (terminal)
.venv/bin/python3 -m PhotoCatalog.explore_duplicates --db catalog.db
.venv/bin/python3 -m PhotoCatalog.explore_duplicates --db catalog.db --report report.html

# Web UI (two-pane browser with image previews)
.venv/bin/python3 -m PhotoCatalog.serve_duplicates --db catalog.db
```

## Dependencies
- `osxphotos` — Mac Photos library access
- `exifread`, `Pillow`, `pillow-heif` — metadata and image handling for folder ingestion
- `imagehash` — perceptual hashing
- `tqdm` — progress bars

## Next steps (in order)
1. **Resolve duplicates** — safe strategy to mark which copy to keep (prefer MacPhotos > Lightroom > Folder, tiebreak by file size), move or record discards
2. **Multi-machine export** — portable DB or sync mechanism so the catalog can be used on different personal machines

## Catalog location
Catalogs are stored in `/Users/andrea/Pictures/PhotoCatalogs/`.
Example: `/Users/andrea/Pictures/PhotoCatalogs/MyCatalog/MyCatalog.db`
