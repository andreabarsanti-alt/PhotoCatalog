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
- `group_id INTEGER`, `strategy TEXT` ('unified'|'phash'|'metadata'|'filename'), `photo_id`, `match_info JSON`, `found_at`
- PRIMARY KEY (strategy, photo_id) — one group per photo per strategy
- Strategies are independent; re-running one clears and rewrites only its rows

**`photos_with_groups` view** — joins photos with its three group IDs as convenience columns (`phash_group_id`, `meta_group_id`, `filename_group_id`)

**`_uf_checkpoint`** — internal table used by `find_unified` to persist scored groups between runs:
- Dropped automatically after a successful apply; presence means a previous run was interrupted after scoring but before committing results

## Key design decisions
- **SQLite not JSON**: the old codebase used JSON files; switching to SQLite enables incremental updates, indexed queries, and joins across sources
- **osxphotos Python library** (not CLI): used for Mac Photos ingestion — abstracts the Photos.sqlite schema which changes between macOS versions
- **exifread not exiftool**: folder ingestion uses pure-Python `exifread`; no external binary dependency
- **original_filename vs file_name**: MacPhotos renames files to UUIDs on disk; `file_name` is the UUID, `original_filename` is what was imported. Both are checked in filename duplicate strategy
- **Rotation-aware metadata matching**: uses MIN/MAX of width/height so portrait and landscape versions match
- **phash(hash_size=16)**: 256-bit hash (64 hex chars). Currently matching is **exact only** — two photos must produce an identical hash string. Hamming distance is NOT used anywhere in the pipeline today.
- **Hashing runs automatically at ingest**: `build_catalog.py` always calls `add_perceptual_hashes()` after ingestion; only files accessible on disk at that moment are hashed
- **Lightroom file_size**: `AgLibraryFileAssetMetadata` and `AgLibraryFileDigest` are empty in this catalog; sizes come from `AgParsedImportHash` (via `id_global`, ~37% coverage) then fall back to `os.stat()` for locally accessible files
- **insert_photos uses union of all row keys**: handles heterogeneous batches correctly
- **Unified duplicate strategy**: union-find across 3 signals (phash, dims+date+type, stem+date_minute), then score each group on 6 binary attributes; score = (10+n) if phash else n; min_score = 2 (internal constant `_MIN_SCORE`, not exposed to users)
- **`caffeinate -i`**: Build Catalog and Find Duplicates subprocesses are wrapped with `caffeinate -i` to prevent macOS idle sleep; Browse Duplicates is NOT (it runs indefinitely)
- **Subprocess window fix**: the GUI spawns `PhotoCatalogCLI` (a plain binary in `Contents/MacOS/`) instead of the main `PhotoCatalog` app binary. Because it is NOT the `CFBundleExecutable`, macOS does not go through app-launch infrastructure for it — no extra Dock icon or window appears
- **Parallel hashing**: `add_perceptual_hashes()` uses `ThreadPoolExecutor` with `min(os.cpu_count(), 8)` threads by default (auto, configurable via `--workers`); commits every 200 hashes instead of per-file

## Resumable operations
Both long-running operations are safe to interrupt and resume:

**Folder ingestion** (`sources/folder.py`):
- Before walking, queries `SELECT source_file FROM photos WHERE source_catalog = ?`
- Filters the file list to only unprocessed files — already-ingested files are skipped entirely (no EXIF extraction, no INSERT attempt)
- Progress bar shows 0→100% of remaining work; prints "Resuming: N already ingested, M remaining"

**Find duplicates** (`find_duplicates.py`, unified strategy only):
- After scoring completes, saves results to `_uf_checkpoint` table and commits
- On next run: checks checkpoint validity (same photo count + same `_MIN_SCORE`); if valid, skips union-find and scoring entirely
- If photo count changed or checkpoint is stale, discards it and runs fresh
- After successful `DELETE + INSERT` into `duplicate_groups`, drops the checkpoint

## Unified scoring (find_duplicates.py)
Signals scored per group:
- `phash` — all photos share the same non-null perceptual hash
- `dims`  — all share rotation-aware (min, max) of width/height
- `date`  — all share exact date_original
- `type`  — all share file_type
- `size`  — all share file_size
- `name`  — at least one common filename stem across all photos

`score = (10 + n_others) if phash else n_others`  (n_others = count of other signals that match)

Groups with score < `_MIN_SCORE` (2) are discarded. Groups inserted in descending score order → group #1 = strongest. Score is not exposed to users — the signal filter badges in the web UI provide equivalent filtering interactively.

## Web UI filters (serve_duplicates.py)
Signal filter badges in the left pane narrow the group list in real time:
- `pHash`, `Dims`, `Date`, `Type`, `Size`, `Name` — standard signal badges (from match_info)
- `Unique` — show groups already reduced to a single survivor (resolved groups)
- `Video` — show only groups containing at least one video file (MOV, MP4, M4V, AVI, MKV…)

`has_video` is computed in the `_all_groups()` SQL query via `MAX(CASE WHEN UPPER(file_type) IN (...) THEN 1 ELSE 0 END)`.

## How to run
```bash
# Always use the venv
.venv/bin/python3 -m PhotoCatalog.build_catalog --source MacPhotos --db catalog.db
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Lightroom --db catalog.db --path /path/to/catalog.lrcat
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Folder    --db catalog.db --path /Volumes/DISK/Photos
# Hashing runs automatically after each ingest; no --hash flag needed.
# Safe to interrupt and re-run — already-ingested files are skipped.

# Find duplicates (unified is the default and recommended)
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db
# Focused single-signal runs (faster, for debugging):
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy phash
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy metadata
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy filename

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
2. **Fuzzy phash matching (Hamming distance)** — add a second hash column (`perceptual_hash_small`, `hash_size=8`, 64-bit / 16 hex chars) alongside the existing `hash_size=16` one. At ingest, compute both. In `find_unified`, add a fourth union-find signal: pairs where `hamming(hash_small_a, hash_small_b) ≤ 10`. This catches near-duplicates (light edits, JPEG re-compression, slight crops) that the current exact-match misses, without replacing the high-precision 256-bit hash. `hash_size=8` is the `imagehash` library's default and sweet spot for fuzzy matching.
3. **Multi-machine export** — portable DB or sync mechanism so the catalog can be used on different personal machines

## Catalog location
Catalogs are stored in `/Users/andrea/Pictures/PhotoCatalogs/`.
Example: `/Users/andrea/Pictures/PhotoCatalogs/MyCatalog/MyCatalog.db`
