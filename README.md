# PhotoCatalog

**Primary goal: find and safely remove duplicate photos spread across multiple sources.**

Builds a unified SQLite catalog from macOS Photos, Lightroom Classic, and disk folders, then identifies duplicates across all sources using three independent strategies (perceptual hash, metadata fingerprint, filename + date).

## Setup

```bash
pip install osxphotos imagehash Pillow pillow-heif
brew install exiftool          # for folder ingestion
```

## Usage

### 1 — Build the catalog

```bash
# macOS Photos library (default library)
python -m PhotoCatalog.build_catalog --source MacPhotos --db catalog.db

# Specific Photos library
python -m PhotoCatalog.build_catalog --source MacPhotos --db catalog.db --path /Volumes/Backup/Photos.photoslibrary

# Lightroom Classic catalog
python -m PhotoCatalog.build_catalog --source Lightroom --db catalog.db --path /path/to/Catalog.lrcat

# Disk folder (recursive)
python -m PhotoCatalog.build_catalog --source Folder --db catalog.db --path /Volumes/DISK/Photos

# All sources are additive — re-run for each source, existing rows are skipped
# Use --fresh to wipe and start over
# Use --hash to also compute perceptual hashes in the same pass (slow)
```

### 2 — Compute perceptual hashes (if not done above)

```bash
python -m PhotoCatalog.build_catalog --source Folder --db catalog.db --path /path --hash
```

### 3 — Find duplicates

```bash
python -m PhotoCatalog.find_duplicates --strategy all --db catalog.db
```

Three independent strategies:

| Strategy | Logic | Notes |
|---|---|---|
| `phash` | Identical perceptual hash | Most reliable; requires hashes computed |
| `metadata` | Same dimensions + date + file type | Rotation-aware (portrait ↔ landscape); fast |
| `filename` | Same filename stem + same capture minute | Handles MacPhotos UUID renaming |

Results are stored in the `duplicate_groups` table and visible via the `photos_with_groups` view. Re-running a strategy only clears and rewrites that strategy's rows.

### Querying the catalog

```bash
sqlite3 catalog.db
```

```sql
-- Photos matched by any strategy
SELECT source_file, source_type, phash_group_id, meta_group_id, filename_group_id
FROM   photos_with_groups
WHERE  phash_group_id IS NOT NULL
    OR meta_group_id  IS NOT NULL
    OR filename_group_id IS NOT NULL;

-- All members of a specific phash duplicate group
SELECT source_file, source_type, file_size, date_original
FROM   photos_with_groups
WHERE  phash_group_id = 5;

-- Cross-source duplicates matched by phash
SELECT p1.source_file, p1.source_type, p2.source_file, p2.source_type
FROM   duplicate_groups d1
JOIN   duplicate_groups d2 ON d1.group_id = d2.group_id AND d1.strategy = d2.strategy AND d1.photo_id < d2.photo_id
JOIN   photos p1 ON d1.photo_id = p1.id
JOIN   photos p2 ON d2.photo_id = p2.id
WHERE  d1.strategy = 'phash'
  AND  p1.source_type != p2.source_type;
```

## Roadmap

- [ ] Duplicate visualizer — browse groups, compare metadata side-by-side
- [ ] Real-catalog testing and debugging
- [ ] Safe duplicate resolution — mark keeper per group, move or record discards
- [ ] Multi-machine export — portable DB or sync for use across personal machines
