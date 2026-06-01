import shutil
import sqlite3
from pathlib import Path

_PHOTOS_TABLE = """
CREATE TABLE IF NOT EXISTS photos (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identity
    source_file              TEXT    NOT NULL UNIQUE,
    source_type              TEXT    NOT NULL,   -- 'MacPhotos' | 'Lightroom' | 'Folder'
    source_catalog           TEXT,               -- path to the catalog/library/folder this photo came from
    file_name                TEXT,               -- filename on disk (UUID-based for MacPhotos)
    original_filename        TEXT,               -- original import name (MacPhotos only)

    -- File
    file_type                TEXT,
    file_size                INTEGER,

    -- Dimensions
    image_width              INTEGER,
    image_height             INTEGER,

    -- Dates (ISO 8601)
    date_original            TEXT,
    date_created             TEXT,
    date_modified            TEXT,

    -- Camera
    make                     TEXT,
    model                    TEXT,
    lens_model               TEXT,
    focal_length             REAL,
    aperture                 REAL,
    iso                      INTEGER,
    shutter_speed            REAL,

    -- Location
    latitude                 REAL,
    longitude                REAL,

    -- Computed (populated by enrich.py)
    perceptual_hash          TEXT,               -- phash(hash_size=16), 64 hex chars — exact matching
    perceptual_hash_small    TEXT,               -- phash(hash_size=8),  16 hex chars — fuzzy Hamming matching

    -- Unified curation (populated from any source that has it)
    caption                  TEXT,
    keywords                 TEXT,               -- JSON array e.g. '["travel","italy"]'
    has_adjustments          INTEGER,            -- 1 if edited in Photos or Lightroom

    -- Mac Photos specific
    mp_uuid                  TEXT,
    mp_favorite              INTEGER,            -- 1 if hearted
    mp_score                 REAL,               -- Apple ML aesthetic score 0-1
    mp_is_live               INTEGER,
    mp_is_burst              INTEGER,

    -- Lightroom curation
    lr_rating                INTEGER,            -- 0-5 stars
    lr_pick                  INTEGER,            -- -1 rejected | 0 unflagged | 1 picked
    lr_color_labels          TEXT,

    -- Lightroom develop settings
    lr_white_balance         TEXT,
    lr_temperature           REAL,
    lr_tint                  REAL,
    lr_exposure              REAL,
    lr_contrast              REAL,
    lr_highlights            REAL,
    lr_shadows               REAL,
    lr_whites                REAL,
    lr_blacks                REAL,
    lr_clarity               REAL,
    lr_vibrance              REAL,
    lr_saturation            REAL,
    lr_sharpness             REAL,
    lr_luminance_smoothing   REAL,
    lr_color_noise_reduction REAL,
    lr_vignette_amount       REAL,
    lr_crop_top              REAL,
    lr_crop_left             REAL,
    lr_crop_bottom           REAL,
    lr_crop_right            REAL,
    lr_crop_angle            REAL,
    lr_post_crop_vignette    REAL,
    lr_dehaze                REAL,
    lr_texture               REAL,
    lr_grayscale             INTEGER,

    ingested_at              TEXT DEFAULT (datetime('now'))
);
"""

_SOURCES_TABLE = """
CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type   TEXT    NOT NULL,              -- 'MacPhotos' | 'Lightroom' | 'Folder'
    source_path   TEXT,                          -- actual path used; NULL = default MacPhotos library
    last_ingested TEXT    DEFAULT (datetime('now')),
    photos_added  INTEGER DEFAULT 0,             -- rows inserted in the most recent run
    UNIQUE (source_type, source_path)
);
"""

_PHOTOS_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_photos_source_catalog
    ON photos (source_catalog);
CREATE INDEX IF NOT EXISTS idx_photos_perceptual_hash
    ON photos (perceptual_hash)
    WHERE perceptual_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_photos_perceptual_hash_small
    ON photos (perceptual_hash_small)
    WHERE perceptual_hash_small IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_photos_meta_signal
    ON photos (image_width, image_height, date_original, file_type)
    WHERE image_width IS NOT NULL AND image_height IS NOT NULL
      AND date_original IS NOT NULL AND file_type IS NOT NULL;
"""

_DUPLICATE_GROUPS_TABLE = """
CREATE TABLE IF NOT EXISTS duplicate_groups (
    group_id   INTEGER NOT NULL,
    strategy   TEXT    NOT NULL,   -- 'phash' | 'metadata' | 'filename'
    photo_id   INTEGER NOT NULL REFERENCES photos(id),
    match_info TEXT,               -- JSON: strategy-specific detail (Hamming distance, stem, etc.)
    found_at   TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (strategy, photo_id)
);
CREATE INDEX IF NOT EXISTS idx_dup_groups_strategy_group
    ON duplicate_groups (strategy, group_id);
"""

_DECISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS decisions (
    photo_id   INTEGER PRIMARY KEY REFERENCES photos(id),
    action     TEXT    NOT NULL CHECK (action IN ('keep', 'discard')),
    decided_at TEXT    DEFAULT (datetime('now'))
);
"""

_PHOTOS_WITH_GROUPS_VIEW = """
CREATE VIEW IF NOT EXISTS photos_with_groups AS
SELECT
    p.*,
    gr.group_id  AS groups_group_id,
    ph.group_id  AS phash_group_id,
    me.group_id  AS meta_group_id,
    fn.group_id  AS filename_group_id
FROM   photos p
LEFT JOIN duplicate_groups gr ON gr.photo_id = p.id AND gr.strategy = 'groups'
LEFT JOIN duplicate_groups ph ON ph.photo_id = p.id AND ph.strategy = 'phash'
LEFT JOIN duplicate_groups me ON me.photo_id = p.id AND me.strategy = 'metadata'
LEFT JOIN duplicate_groups fn ON fn.photo_id = p.id AND fn.strategy = 'filename';
"""


def is_external_path(path: str) -> bool:
    """True when the path lives on a /Volumes/ mount (external or network drive)."""
    try:
        return Path(path).resolve().parts[:2] == ('/', 'Volumes')
    except Exception:
        return False


def cache_db_locally(db_path: str) -> str:
    """
    Copy a DB from an external drive to ~/Library/Caches/PhotoCatalog/ and
    return the local cache path.  Checkpoints the WAL first so the copy is
    self-contained.  If the path is already local, returns db_path unchanged.

    If a local cache already exists and is newer than the external copy (e.g.
    because a previous run was killed before sync-back), the local cache is
    reused and a warning is printed rather than overwriting uncommitted work.
    """
    if not is_external_path(db_path):
        return db_path
    cache_dir = Path.home() / "Library" / "Caches" / "PhotoCatalog"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = str(cache_dir / Path(db_path).name)

    # If the local cache is newer the external was never synced back — reuse it.
    if Path(cache_path).exists():
        local_mtime    = Path(cache_path).stat().st_mtime
        external_mtime = Path(db_path).stat().st_mtime
        if local_mtime > external_mtime + 2:   # 2-second grace for filesystem clock skew
            print(
                f"WARNING: local cache is newer than {Path(db_path).name} on the external drive.\n"
                f"  Using local cache (a previous run may not have synced back).\n"
                f"  Delete {cache_path} to force a fresh copy from the drive."
            )
            return cache_path

    print(f"External drive detected — caching {Path(db_path).name} locally…")
    _src = sqlite3.connect(db_path)
    try:
        _src.execute("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        pass  # proceed with copy even if checkpoint fails; SQLite will recover
    finally:
        _src.close()
    shutil.copy2(db_path, cache_path)
    return cache_path


def sync_db_back(cache_path: str, original_path: str) -> None:
    """Checkpoint the local cache and copy it back to the original external path."""
    _c = sqlite3.connect(cache_path)
    _c.execute("PRAGMA wal_checkpoint(FULL)")
    _c.close()
    print(f"Syncing catalog back to {original_path} …")
    shutil.copy2(cache_path, original_path)


class _CachedConnection:
    """
    Wraps a sqlite3.Connection opened on a local cache copy.
    On close(), checkpoints the WAL and syncs the file back to the original path.
    Used by connect() for single-connection CLI operations (ingest, hash, find).
    """
    def __init__(self, conn: sqlite3.Connection, cache_path: str, original_path: str):
        self._conn          = conn
        self._cache_path    = cache_path
        self._original_path = original_path

    def close(self):
        try:
            self._conn.execute("PRAGMA wal_checkpoint(FULL)")
        except Exception:
            pass
        self._conn.close()
        sync_db_back(self._cache_path, self._original_path)

    def sync_checkpoint(self) -> None:
        """WAL checkpoint + copy to external drive without closing. Safe to call mid-operation."""
        try:
            self._conn.execute("PRAGMA wal_checkpoint(FULL)")
        except Exception:
            pass
        sync_db_back(self._cache_path, self._original_path)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


def connect(db_path: str) -> sqlite3.Connection:
    """
    Open a SQLite connection with tuned PRAGMAs.

    If db_path is on an external /Volumes/ mount the database is first copied to
    ~/Library/Caches/PhotoCatalog/ and the returned connection points to that
    local copy.  On close() the local copy is synced back automatically, so all
    single-connection CLI operations (ingest, hashing, find-duplicates) run at
    SSD speed without any code changes at call sites.

    serve_duplicates opens many short-lived connections per HTTP request, so it
    manages the cache lifecycle itself via cache_db_locally() / sync_db_back().
    """
    actual_path = cache_db_locally(db_path) if is_external_path(db_path) and Path(db_path).exists() else db_path
    conn = sqlite3.connect(actual_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL; ~2x faster writes
    conn.execute("PRAGMA cache_size=-65536")    # 64 MB page cache
    conn.execute("PRAGMA mmap_size=268435456")  # 256 MB memory-mapped I/O
    conn.execute("PRAGMA temp_store=MEMORY")    # temp tables in RAM
    if actual_path != db_path:
        return _CachedConnection(conn, actual_path, db_path)  # type: ignore[return-value]
    return conn


def init_db(conn: sqlite3.Connection, drop_existing: bool = False) -> None:
    if drop_existing:
        conn.execute("DROP VIEW  IF EXISTS photos_with_groups")
        conn.execute("DROP TABLE IF EXISTS duplicate_groups")
        conn.execute("DROP TABLE IF EXISTS decisions")
        conn.execute("DROP TABLE IF EXISTS photos")
        conn.execute("DROP TABLE IF EXISTS sources")
        conn.commit()
    conn.executescript(_PHOTOS_TABLE)
    # Column migrations must run before indexes that reference those columns
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(photos)")}
    if "source_catalog" not in existing_cols:
        conn.execute("ALTER TABLE photos ADD COLUMN source_catalog TEXT")
    if "perceptual_hash_small" not in existing_cols:
        conn.execute("ALTER TABLE photos ADD COLUMN perceptual_hash_small TEXT")
    conn.commit()
    conn.executescript(_PHOTOS_INDEXES)
    conn.executescript(_SOURCES_TABLE)
    conn.executescript(_DUPLICATE_GROUPS_TABLE)
    conn.executescript(_DECISIONS_TABLE)
    conn.executescript(_PHOTOS_WITH_GROUPS_VIEW)


def ensure_decisions_table(conn: sqlite3.Connection) -> None:
    """Create the decisions table if it doesn't exist (safe migration for existing DBs)."""
    conn.executescript(_DECISIONS_TABLE)
    conn.commit()


def record_source(conn: sqlite3.Connection, source_type: str, source_path: str | None, photos_added: int) -> None:
    """Upsert a row in sources — one row per (source_type, source_path), updated on each run."""
    conn.execute("""
        INSERT INTO sources (source_type, source_path, last_ingested, photos_added)
        VALUES (?, ?, datetime('now'), ?)
        ON CONFLICT (source_type, source_path)
        DO UPDATE SET last_ingested = datetime('now'), photos_added = excluded.photos_added
    """, (source_type, source_path, photos_added))
    conn.commit()


def insert_photos(conn: sqlite3.Connection, rows: list[dict], on_conflict: str = "ignore") -> tuple[int, int]:
    """Insert rows into photos table. Returns (inserted, skipped)."""
    if not rows:
        return 0, 0

    # Union of all keys across rows so heterogeneous batches are handled correctly
    cols = sorted({k for r in rows for k in r if k != "id"})
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    sql = f"INSERT OR {on_conflict.upper()} INTO photos ({col_names}) VALUES ({placeholders})"

    data = [[r.get(c) for c in cols] for r in rows]
    cursor = conn.executemany(sql, data)
    conn.commit()

    inserted = cursor.rowcount
    skipped = len(rows) - inserted
    return inserted, skipped
