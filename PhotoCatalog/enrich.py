"""Enrichment operations that add computed fields to an existing catalog database."""
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from tqdm import tqdm

_IMAGE_TYPES = {"JPEG", "JPG", "PNG", "HEIC", "HEIF", "TIFF", "WEBP", "BMP", "GIF", "RAW",
                "CR2", "CR3", "NEF", "ARW", "ORF", "RW2", "DNG", "RAF"}
_MOVIE_TYPES = {"MOV", "MP4", "M4V", "AVI", "MKV", "MPEG", "MPG", "3GP", "WEBM"}

_COMMIT_BATCH = 200  # commit to DB after this many successful hashes


def add_perceptual_hashes(conn: sqlite3.Connection, workers: int = 0) -> tuple[int, int]:
    """
    Compute and store perceptual hashes for image photos where perceptual_hash is NULL.

    Movies are skipped — perceptual hashing does not apply to video files.
    Cloud-only photos (source_file starting with 'macphotos://') are also skipped
    since the file is not locally accessible.

    Uses phash with hash_size=16 (256-bit hash, 64 hex chars).
    Hamming distance <= 10 is a good "likely duplicate" threshold.

    Args:
        workers: Parallel threads for image decode + hash (0 = auto, capped at 8).

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

    pending = [(row["id"], row["source_file"])
               for row in rows if Path(row["source_file"]).is_file()]
    hashed = 0
    failed = len(rows) - len(pending)  # inaccessible files

    if not pending:
        return hashed, failed

    n_workers = workers if workers > 0 else min(os.cpu_count() or 4, 8)

    def _hash_one(args: tuple) -> tuple[int, str | None, str | None]:
        row_id, path = args
        try:
            img = Image.open(path)
            return row_id, str(imagehash.phash(img, hash_size=16)), None
        except Exception as e:
            return row_id, None, f"{type(e).__name__}: {e}"

    pending_commit = 0

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        label = f"Hashing {len(pending)} images ({n_workers} threads)"
        futures_map = {pool.submit(_hash_one, item): item for item in pending}
        with tqdm(total=len(pending), desc=label, unit="img") as bar:
            for future in as_completed(futures_map):
                row_id, h, err = future.result()
                if h is not None:
                    conn.execute(
                        "UPDATE photos SET perceptual_hash = ? WHERE id = ?",
                        (h, row_id),
                    )
                    hashed += 1
                    pending_commit += 1
                    if pending_commit >= _COMMIT_BATCH:
                        conn.commit()
                        pending_commit = 0
                else:
                    tqdm.write(f"  Hash failed: {futures_map[future][1]} — {err}")
                    failed += 1
                bar.update(1)

    if pending_commit:
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


def main() -> None:
    import argparse
    from .db import connect

    parser = argparse.ArgumentParser(description="Compute perceptual hashes for a catalog.")
    parser.add_argument("--db", required=True, help="Path to the SQLite catalog database file.")
    parser.add_argument(
        "--workers", type=int, default=0,
        help="Hash threads (0 = auto, default: min(cpu_count, 8))."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear all existing perceptual hashes and recompute from scratch."
    )
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        if args.reset:
            conn.execute("UPDATE photos SET perceptual_hash = NULL")
            conn.commit()
            print("Reset: cleared all existing perceptual hashes.")
        hashed, failed = add_perceptual_hashes(conn, workers=args.workers)
        print(f"Hashed : {hashed}")
        if failed:
            print(f"Failed : {failed} (unreadable files)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
