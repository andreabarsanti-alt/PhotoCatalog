"""Ingest from an Adobe Lightroom Classic catalog (.lrcat file, which is SQLite)."""
import json
import sqlite3
from pathlib import Path

from tqdm import tqdm

from ..db import insert_photos

# Notes on the Lightroom schema:
# - captureTime is ISO 8601: "2022-03-02T10:15:22"
# - GPS coords are signed decimals
# - aperture is the f-number (e.g. 2.8)
# - shutterSpeed is in seconds (e.g. 0.001 = 1/1000s)
# - FK column names changed across LR versions:
#     cameraModelRef (not cameraModel), lensRef (not lens)
# - fileSize: AgLibraryFileAssetMetadata / AgLibraryFileDigest are often empty;
#   AgParsedImportHash (joined via id_global) covers ~37% of photos.
#   Python falls back to os.stat() for any row the catalog join misses.
# - Develop settings (exposure, contrast, etc.) are no longer individual columns;
#   they live in Adobe_imageDevelopSettings.text as a serialised blob.
#   Only whiteBalance, grayscale, and hasDevelopAdjustments remain as columns.
QUERY = """
SELECT
    rf.absolutePath || f.pathFromRoot || lf.baseName || '.' || lf.extension  AS source_file,
    lf.baseName || '.' || lf.extension                                        AS file_name,
    UPPER(lf.extension)                                                        AS file_type,
    CAST(ih.fileSize AS INTEGER)                                               AS file_size,
    i.fileWidth                                                                AS image_width,
    i.fileHeight                                                               AS image_height,
    i.captureTime                                                              AS date_original,

    -- Curation
    i.rating                                                                   AS lr_rating,
    i.colorLabels                                                              AS lr_color_labels,
    i.pick                                                                     AS lr_pick,

    -- Camera / EXIF
    cam.value                                                                  AS model,
    lens.value                                                                 AS lens_model,
    exif.focalLength                                                           AS focal_length,
    exif.aperture                                                              AS aperture,
    exif.isoSpeedRating                                                        AS iso,
    exif.shutterSpeed                                                          AS shutter_speed,
    exif.gpsLatitude                                                           AS latitude,
    exif.gpsLongitude                                                          AS longitude,

    -- Keywords (comma-separated; converted to JSON array in Python)
    (SELECT GROUP_CONCAT(kw.name, ',')
     FROM   AgLibraryKeywordImage kwi
     JOIN   AgLibraryKeyword kw ON kwi.tag = kw.id_local
     WHERE  kwi.image = i.id_local)                                            AS keywords_raw,

    -- Develop settings (only fields available as columns in modern LR catalogs;
    -- exposure/contrast/etc. are stored in the text blob, not individual columns)
    dev.whiteBalance                                                           AS lr_white_balance,
    dev.grayscale                                                              AS lr_grayscale,
    dev.hasDevelopAdjustments                                                  AS lr_has_adjustments

FROM      Adobe_images               i
JOIN      AgLibraryFile              lf   ON i.rootFile          = lf.id_local
JOIN      AgLibraryFolder            f    ON lf.folder            = f.id_local
JOIN      AgLibraryRootFolder        rf   ON f.rootFolder         = rf.id_local
LEFT JOIN AgParsedImportHash         ih   ON ih.id_global         = lf.id_global
LEFT JOIN AgHarvestedExifMetadata    exif ON i.id_local           = exif.image
LEFT JOIN AgInternedExifCameraModel  cam  ON exif.cameraModelRef  = cam.id_local
LEFT JOIN AgInternedExifLens         lens ON exif.lensRef         = lens.id_local
LEFT JOIN Adobe_imageDevelopSettings dev  ON i.id_local           = dev.image
WHERE rf.absolutePath IS NOT NULL
  AND lf.baseName     IS NOT NULL
"""


def _lr_find_image_ids(lr_conn: sqlite3.Connection, source_files: list[str]) -> dict[str, int]:
    """Return {source_file: id_local} for photos found in the Lightroom catalog.

    Uses a temp table + single join instead of one query per file.
    """
    if not source_files:
        return {}
    lr_conn.execute("CREATE TEMP TABLE IF NOT EXISTS _pc_paths (path TEXT PRIMARY KEY)")
    lr_conn.execute("DELETE FROM _pc_paths")
    lr_conn.executemany("INSERT OR IGNORE INTO _pc_paths VALUES (?)", [(sf,) for sf in source_files])
    rows = lr_conn.execute("""
        SELECT rf.absolutePath || f.pathFromRoot || lf.baseName || '.' || lf.extension AS source_file,
               i.id_local
        FROM   Adobe_images i
        JOIN   AgLibraryFile lf        ON i.rootFile    = lf.id_local
        JOIN   AgLibraryFolder f       ON lf.folder     = f.id_local
        JOIN   AgLibraryRootFolder rf  ON f.rootFolder  = rf.id_local
        JOIN   _pc_paths tp            ON tp.path = rf.absolutePath || f.pathFromRoot || lf.baseName || '.' || lf.extension
    """).fetchall()
    return {row["source_file"]: row["id_local"] for row in rows}


def add_to_collection(lrcat_path: str, source_files: list[str],
                      collection_name: str) -> tuple[int, list[str]]:
    """Add photos to a Lightroom collection, creating it if needed.

    Returns (added_count, skipped_source_files).
    Lightroom must be closed before calling this.
    """
    import uuid as _uuid
    lr_conn = sqlite3.connect(str(lrcat_path))
    lr_conn.row_factory = sqlite3.Row
    try:
        col = lr_conn.execute(
            "SELECT id_local FROM AgLibraryCollection WHERE name = ? AND parent IS NULL",
            (collection_name,),
        ).fetchone()
        if col is None:
            col_global = _uuid.uuid4().hex
            cur = lr_conn.execute(
                """INSERT INTO AgLibraryCollection
                       (creationId, genealogy, id_global, imageCount, name, parent, systemOnly)
                   VALUES (?, '', ?, 0, ?, NULL, 0)""",
                (f"photocatalog-{col_global[:8]}", col_global, collection_name),
            )
            col_id = cur.lastrowid
            lr_conn.execute(
                "UPDATE AgLibraryCollection SET genealogy = ? WHERE id_local = ?",
                (f"/{col_id}/", col_id),
            )
        else:
            col_id = col["id_local"]

        img_ids = _lr_find_image_ids(lr_conn, source_files)
        skipped = [sf for sf in source_files if sf not in img_ids]

        img_id_list = list(img_ids.values())
        # Single query to find which images are already in the collection
        already_in: set[int] = set()
        for i in range(0, len(img_id_list), 900):
            chunk = img_id_list[i:i + 900]
            ph = ",".join("?" * len(chunk))
            for row in lr_conn.execute(
                f"SELECT image FROM AgLibraryCollectionImage WHERE collection = ? AND image IN ({ph})",
                [col_id] + chunk,
            ):
                already_in.add(row[0])

        to_add = [img_id for img_id in img_id_list if img_id not in already_in]
        if to_add:
            max_pos = lr_conn.execute(
                "SELECT COALESCE(MAX(positionInCollection), 0) FROM AgLibraryCollectionImage WHERE collection = ?",
                (col_id,),
            ).fetchone()[0]
            lr_conn.executemany(
                "INSERT INTO AgLibraryCollectionImage (collection, image, pick, positionInCollection) VALUES (?, ?, 0, ?)",
                [(col_id, img_id, pos) for img_id, pos in
                 zip(to_add, range(max_pos + 1, max_pos + 1 + len(to_add)))],
            )
        added = len(to_add)
        lr_conn.execute(
            "UPDATE AgLibraryCollection SET imageCount = (SELECT COUNT(*) FROM AgLibraryCollectionImage WHERE collection = ?) WHERE id_local = ?",
            (col_id, col_id),
        )
        lr_conn.commit()
        return added, skipped
    finally:
        lr_conn.close()


def add_keyword(lrcat_path: str, source_files: list[str],
                keyword_name: str) -> tuple[int, list[str]]:
    """Add a keyword tag to photos in a Lightroom catalog, creating the keyword if needed.

    Returns (tagged_count, skipped_source_files).
    Lightroom must be closed before calling this.
    """
    import uuid as _uuid
    lr_conn = sqlite3.connect(str(lrcat_path))
    lr_conn.row_factory = sqlite3.Row
    try:
        kw = lr_conn.execute(
            "SELECT id_local FROM AgLibraryKeyword WHERE name = ? AND parent IS NULL",
            (keyword_name,),
        ).fetchone()
        if kw is None:
            kw_global = _uuid.uuid4().hex
            lc = keyword_name.lower()
            cur = lr_conn.execute(
                """INSERT INTO AgLibraryKeyword
                       (dateCreated, genealogy, id_global, includedInAutoSync,
                        keyAssetFace, keyFace, keyImage, lastApplied,
                        lc_name, name, parent, searchIndex, synonyms)
                   VALUES (datetime('now'), '', ?, 0,
                           NULL, NULL, NULL, NULL,
                           ?, ?, NULL, ?, NULL)""",
                (kw_global, lc, keyword_name, lc),
            )
            kw_id = cur.lastrowid
            lr_conn.execute(
                "UPDATE AgLibraryKeyword SET genealogy = ? WHERE id_local = ?",
                (f"/{kw_id}/", kw_id),
            )
        else:
            kw_id = kw["id_local"]

        img_ids = _lr_find_image_ids(lr_conn, source_files)
        skipped = [sf for sf in source_files if sf not in img_ids]

        img_id_list = list(img_ids.values())
        # Single query to find which images already have this keyword
        already_tagged: set[int] = set()
        for i in range(0, len(img_id_list), 900):
            chunk = img_id_list[i:i + 900]
            ph = ",".join("?" * len(chunk))
            for row in lr_conn.execute(
                f"SELECT image FROM AgLibraryKeywordImage WHERE tag = ? AND image IN ({ph})",
                [kw_id] + chunk,
            ):
                already_tagged.add(row[0])

        to_add = [(img_id, kw_id) for img_id in img_id_list if img_id not in already_tagged]
        if to_add:
            lr_conn.executemany("INSERT INTO AgLibraryKeywordImage (image, tag) VALUES (?, ?)", to_add)
            lr_conn.execute(
                "UPDATE AgLibraryKeyword SET imageCount = COALESCE(imageCount, 0) + ? WHERE id_local = ?",
                (len(to_add), kw_id),
            )
        lr_conn.commit()
        return len(to_add), skipped
    finally:
        lr_conn.close()


def ingest(conn: sqlite3.Connection, lrcat_path: str) -> tuple[int, int]:
    """
    Ingest photos from a Lightroom Classic catalog.

    Args:
        conn: SQLite connection to the catalog database.
        lrcat_path: Path to the .lrcat file.

    Returns:
        (inserted, skipped) counts.
    """
    lrcat = Path(lrcat_path)
    if not lrcat.exists():
        raise FileNotFoundError(f"Lightroom catalog not found: {lrcat}")

    lr_conn = sqlite3.connect(f"file:{lrcat}?mode=ro", uri=True)
    lr_conn.row_factory = sqlite3.Row
    try:
        print("Querying Lightroom catalog...", end=" ", flush=True)
        cursor = lr_conn.execute(QUERY)
        raw_rows = cursor.fetchall()
        print(f"{len(raw_rows)} photos")
    finally:
        lr_conn.close()

    inserted_total = 0
    skipped_total = 0
    batch: list[dict] = []

    for r in tqdm(raw_rows, desc="Processing rows", unit="photo"):
        source_file = r["source_file"]
        if not source_file:
            continue

        keywords_raw = r["keywords_raw"]
        keywords = json.dumps(keywords_raw.split(",")) if keywords_raw else None

        file_size = r["file_size"]
        if file_size is None:
            p = Path(source_file)
            if p.is_file():
                file_size = p.stat().st_size

        batch.append({
            "source_file":      source_file,
            "source_type":      "Lightroom",
            "source_catalog":   str(lrcat),
            "file_name":        r["file_name"],
            "file_type":        r["file_type"],
            "file_size":        file_size,
            "image_width":      r["image_width"],
            "image_height":     r["image_height"],
            "date_original":    r["date_original"],
            "model":            r["model"],
            "lens_model":       r["lens_model"],
            "focal_length":     r["focal_length"],
            "aperture":         r["aperture"],
            "iso":              r["iso"],
            "shutter_speed":    r["shutter_speed"],
            "latitude":         r["latitude"],
            "longitude":        r["longitude"],
            "keywords":         keywords,
            "has_adjustments":  r["lr_has_adjustments"],
            "lr_rating":        r["lr_rating"],
            "lr_color_labels":  r["lr_color_labels"],
            "lr_pick":          r["lr_pick"],
            "lr_white_balance": r["lr_white_balance"],
            "lr_grayscale":     r["lr_grayscale"],
        })

        if len(batch) >= 200:
            ins, skp = insert_photos(conn, batch)
            inserted_total += ins
            skipped_total += skp
            batch = []

    if batch:
        ins, skp = insert_photos(conn, batch)
        inserted_total += ins
        skipped_total += skp

    return inserted_total, skipped_total
