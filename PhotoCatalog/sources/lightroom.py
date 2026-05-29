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

    rows = []
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

        rows.append({
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

    return insert_photos(conn, rows)
