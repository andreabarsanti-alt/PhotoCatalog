import sqlite3

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
    perceptual_hash          TEXT,               -- phash(hash_size=16), 64 hex chars

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
    ph.group_id  AS phash_group_id,
    me.group_id  AS meta_group_id,
    fn.group_id  AS filename_group_id
FROM   photos p
LEFT JOIN duplicate_groups ph ON ph.photo_id = p.id AND ph.strategy = 'phash'
LEFT JOIN duplicate_groups me ON me.photo_id = p.id AND me.strategy = 'metadata'
LEFT JOIN duplicate_groups fn ON fn.photo_id = p.id AND fn.strategy = 'filename';
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection, drop_existing: bool = False) -> None:
    if drop_existing:
        conn.execute("DROP VIEW  IF EXISTS photos_with_groups")
        conn.execute("DROP TABLE IF EXISTS duplicate_groups")
        conn.execute("DROP TABLE IF EXISTS photos")
        conn.execute("DROP TABLE IF EXISTS sources")
        conn.commit()
    conn.executescript(_PHOTOS_TABLE)
    conn.executescript(_SOURCES_TABLE)
    conn.executescript(_DUPLICATE_GROUPS_TABLE)
    conn.executescript(_DECISIONS_TABLE)
    conn.executescript(_PHOTOS_WITH_GROUPS_VIEW)
    # Migrate existing databases that predate source_catalog
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(photos)")}
    if "source_catalog" not in existing_cols:
        conn.execute("ALTER TABLE photos ADD COLUMN source_catalog TEXT")
    conn.commit()


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
