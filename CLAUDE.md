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
        folder.py       # Ingest via exiftool subprocess
archive/                # Old JSON-based codebase (reference only)
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

## Key design decisions
- **SQLite not JSON**: the old codebase used JSON files; switching to SQLite enables incremental updates, indexed queries, and joins across sources
- **osxphotos Python library** (not CLI): used for Mac Photos ingestion — abstracts the Photos.sqlite schema which changes between macOS versions
- **original_filename vs file_name**: MacPhotos renames files to UUIDs on disk; `file_name` is the UUID, `original_filename` is what was imported. Both are checked in filename duplicate strategy
- **Rotation-aware metadata matching**: uses MIN/MAX of width/height so portrait and landscape versions match
- **phash(hash_size=16)**: 256-bit hash (64 hex chars), Hamming distance ≤ 10 = likely duplicate
- **Hashing runs automatically at ingest**: `build_catalog.py` always calls `add_perceptual_hashes()` after ingestion; only files accessible on disk at that moment are hashed
- **Lightroom file_size**: `AgLibraryFileAssetMetadata` and `AgLibraryFileDigest` are empty in this catalog; sizes come from `AgParsedImportHash` (via `id_global`, ~37% coverage) then fall back to `os.stat()` for locally accessible files
- **insert_photos uses union of all row keys**: handles heterogeneous batches correctly
- **Unified duplicate strategy**: union-find across 3 signals (phash, dims+date+type, stem+date_minute), then score each group on 6 binary attributes; score = (10+n) if phash else n

## Unified scoring (find_duplicates.py)
Signals scored per group:
- `phash` — all photos share the same non-null perceptual hash
- `dims`  — all share rotation-aware (min, max) of width/height
- `date`  — all share exact date_original
- `type`  — all share file_type
- `size`  — all share file_size
- `name`  — at least one common filename stem across all photos

`score = (10 + n_others) if phash else n_others`  (n_others = count of other signals that match)

Groups with score < 2 are discarded. Groups inserted in descending score order → group #1 = strongest.

## How to run
```bash
# Always use the venv
.venv/bin/python3 -m PhotoCatalog.build_catalog --source MacPhotos --db catalog.db
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Lightroom --db catalog.db --path /path/to/catalog.lrcat
.venv/bin/python3 -m PhotoCatalog.build_catalog --source Folder    --db catalog.db --path /Volumes/DISK/Photos
# Hashing runs automatically after each ingest; no --hash flag needed.

# Find duplicates (unified strategy is the default and recommended)
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db
.venv/bin/python3 -m PhotoCatalog.find_duplicates --db catalog.db --strategy all  # also runs phash/metadata/filename

# Browse duplicate groups (terminal)
.venv/bin/python3 -m PhotoCatalog.explore_duplicates --db catalog.db
.venv/bin/python3 -m PhotoCatalog.explore_duplicates --db catalog.db --strategy unified
.venv/bin/python3 -m PhotoCatalog.explore_duplicates --db catalog.db --report report.html

# Web UI (two-pane browser with image previews)
.venv/bin/python3 -m PhotoCatalog.serve_duplicates --db catalog.db
```

## Dependencies
- `osxphotos` — Mac Photos library access
- `exiftool` (CLI) — metadata extraction for folder ingestion
- `imagehash`, `Pillow`, `pillow-heif` — perceptual hashing

## Next steps (in order)
1. **Resolve duplicates** — safe strategy to mark which copy to keep (prefer MacPhotos > Lightroom > Folder, tiebreak by file size), move or record discards
2. **Multi-machine export** — portable DB or sync mechanism so the catalog can be used on different personal machines

## Catalog location
Catalogs are stored in `/Users/andrea/Pictures/PhotoCatalogs/`.
Example: `/Users/andrea/Pictures/PhotoCatalogs/MyCatalog/MyCatalog.db`
