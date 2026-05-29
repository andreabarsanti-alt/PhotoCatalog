"""Enrichment operations that add computed fields to an existing catalog database."""
import sqlite3
from pathlib import Path
from typing import Optional

from tqdm import tqdm

_IMAGE_TYPES = {"JPEG", "JPG", "PNG", "HEIC", "HEIF", "TIFF", "WEBP", "BMP", "GIF", "RAW",
                "CR2", "CR3", "NEF", "ARW", "ORF", "RW2", "DNG", "RAF"}
_MOVIE_TYPES = {"MOV", "MP4", "M4V", "AVI", "MKV", "MPEG", "MPG", "3GP", "WEBM"}


def add_perceptual_hashes(conn: sqlite3.Connection, batch_size: int = 500) -> tuple[int, int]:
    """
    Compute and store perceptual hashes for image photos where perceptual_hash is NULL.

    Movies are skipped — perceptual hashing does not apply to video files.
    Cloud-only photos (source_file starting with 'macphotos://') are also skipped
    since the file is not locally accessible.

    Uses phash with hash_size=16 (256-bit hash, 64 hex chars).
    Hamming distance <= 10 is a good "likely duplicate" threshold.

    Returns:
        (hashed, failed) counts.
    """
    try:
        import imagehash
        from PIL import Image
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError as e:
        raise ImportError(f"Required: pip install imagehash Pillow pillow-heif  —  {e}")

    rows = conn.execute("""
        SELECT id, source_file, file_type
        FROM   photos
        WHERE  perceptual_hash IS NULL
          AND  source_file NOT LIKE 'macphotos://%'
          AND  UPPER(COALESCE(file_type, '')) NOT IN ({})
    """.format(", ".join(f"'{t}'" for t in _MOVIE_TYPES))).fetchall()

    total = len(rows)
    hashed = 0
    failed = 0

    for i, row in enumerate(tqdm(rows, desc=f"Hashing {total} images", unit="img")):
        if not Path(row["source_file"]).is_file():
            failed += 1
            continue
        try:
            img = Image.open(row["source_file"])
            h = str(imagehash.phash(img, hash_size=16))
            conn.execute(
                "UPDATE photos SET perceptual_hash = ? WHERE id = ?",
                (h, row["id"]),
            )
            hashed += 1
        except Exception as e:
            tqdm.write(f"  Hash failed: {row['source_file']} — {type(e).__name__}: {e}")
            failed += 1

        if i % batch_size == 0:
            conn.commit()

    conn.commit()
    return hashed, failed


def download_cloud_photos(
    conn: sqlite3.Connection,
    library_path: Optional[str],
    download_dir: str,
    limit: Optional[int] = None,
) -> tuple[int, int]:
    """
    Download iCloud-only MacPhotos entries to a local folder and update source_file.

    Requires Photos.app to be running (uses AppleScript via osxphotos).
    Photos already downloaded (source_file not starting with 'macphotos://') are skipped,
    making the operation safe to re-run in batches.

    Args:
        conn:         SQLite connection to the catalog database.
        library_path: Path to .photoslibrary bundle; None = default library.
        download_dir: Folder where downloaded originals will be saved.
        limit:        Max number of photos to download in this run (None = all).

    Returns:
        (downloaded, failed) counts.
    """
    try:
        import osxphotos
    except ImportError:
        raise ImportError("osxphotos is required: pip install osxphotos")

    dest = Path(download_dir)
    dest.mkdir(parents=True, exist_ok=True)

    # Find cloud-only rows, optionally limited
    query = """
        SELECT id, mp_uuid, original_filename
        FROM   photos
        WHERE  source_file LIKE 'macphotos://%'
          AND  source_type = 'MacPhotos'
        ORDER  BY id
    """
    if limit:
        query += f" LIMIT {limit}"

    pending = conn.execute(query).fetchall()
    total = len(pending)
    print(f"Downloading {total} iCloud photos to {dest} ...")
    print("  Note: Photos.app must be running for iCloud download to work.")

    # Build UUID -> PhotoInfo map
    kwargs = {"dbfile": library_path} if library_path else {}
    db = osxphotos.PhotosDB(**kwargs)
    uuid_map = {p.uuid: p for p in db.photos(movies=True)}

    downloaded = 0
    failed = 0

    for i, row in enumerate(pending):
        if i > 0 and i % 10 == 0:
            print(f"  {i}/{total} ({downloaded} ok, {failed} failed)...")

        photo = uuid_map.get(row["mp_uuid"])
        if not photo:
            failed += 1
            continue

        try:
            # use_photos_export=True triggers iCloud download via Photos.app
            exported = photo.export(
                str(dest),
                use_photos_export=True,
                overwrite=False,
                increment=True,   # adds _1, _2 suffix on filename collision
                timeout=120,
            )
            if exported:
                new_path = exported[0]
                conn.execute(
                    "UPDATE photos SET source_file = ? WHERE id = ?",
                    (new_path, row["id"]),
                )
                conn.commit()
                downloaded += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  Failed {row['original_filename']} ({row['mp_uuid']}): {e}")
            failed += 1

    return downloaded, failed
