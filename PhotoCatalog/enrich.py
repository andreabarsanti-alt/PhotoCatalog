"""Enrichment operations that add computed fields to an existing catalog database."""
import os
import sqlite3
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from tqdm import tqdm

_IMAGE_TYPES = {"JPEG", "JPG", "PNG", "HEIC", "HEIF", "TIFF", "WEBP", "BMP", "GIF", "RAW",
                "CR2", "CR3", "NEF", "ARW", "ORF", "RW2", "DNG", "RAF"}
_MOVIE_TYPES = {"MOV", "MP4", "M4V", "AVI", "MKV", "MPEG", "MPG", "3GP", "WEBM"}

_COMMIT_BATCH = 200   # commit to DB after this many successful hashes
_RATE_WINDOW  = 10    # rolling-average window for rate/ETA display

# tqdm bar format: drop built-in rate and remaining; we inject our own via postfix
_BAR_FMT = "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}{postfix}]"


def _pending_rows(conn: sqlite3.Connection, where_col: str) -> list:
    """Return accessible image rows where `where_col` IS NULL."""
    rows = conn.execute("""
        SELECT id, source_file
        FROM   photos
        WHERE  {col} IS NULL
          AND  source_file NOT LIKE 'macphotos://%'
          AND  UPPER(COALESCE(file_type, '')) NOT IN ({movies})
    """.format(col=where_col,
               movies=", ".join(f"'{t}'" for t in _MOVIE_TYPES))).fetchall()
    return [r for r in rows if Path(r["source_file"]).is_file()]


def _run_hash_pool(pending: list, n_workers: int, hash_fn, update_sql: str,
                   conn: sqlite3.Connection, label: str) -> tuple[int, int]:
    """Generic worker pool: open image, call hash_fn, UPDATE via update_sql."""
    hashed = 0
    failed = 0
    pending_commit = 0

    def _hash_one(args: tuple):
        row_id, path = args
        try:
            from PIL import Image
            return row_id, hash_fn(Image.open(path)), None
        except Exception as e:
            return row_id, None, f"{type(e).__name__}: {e}"

    rate_times: deque = deque(maxlen=_RATE_WINDOW)

    def _update_rate(bar: tqdm, done: int) -> None:
        """Recompute rate/ETA from the rolling window and inject into the bar postfix."""
        rate_times.append(time.monotonic())
        if len(rate_times) < 2:
            return
        dt = rate_times[-1] - rate_times[0]
        rate = (len(rate_times) - 1) / dt if dt > 0 else 0
        left = len(pending) - done
        if rate > 0 and left > 0:
            eta_s = int(left / rate)
            mins, secs = divmod(eta_s, 60)
            eta_str = f"<{mins}:{secs:02d}" if mins else f"<{secs}s"
        else:
            eta_str = ""
        bar.set_postfix_str(f"{rate:.1f}img/s  {eta_str}", refresh=False)

    pool = ThreadPoolExecutor(max_workers=n_workers)
    try:
        futures_map = {pool.submit(_hash_one, item): item for item in pending}
        with tqdm(total=len(pending), desc=label, unit="img", bar_format=_BAR_FMT) as bar:
            for future in as_completed(futures_map):
                row_id, h, err = future.result()
                if h is not None:
                    conn.execute(update_sql, (h, row_id))
                    hashed += 1
                    pending_commit += 1
                    if pending_commit >= _COMMIT_BATCH:
                        conn.commit()
                        pending_commit = 0
                else:
                    tqdm.write(f"  Hash failed: {futures_map[future][1]} — {err}")
                    failed += 1
                _update_rate(bar, hashed + failed)
                bar.update(1)
        pool.shutdown(wait=True)
    except BaseException:
        # Cancel pending futures so we stop quickly; wait only for already-running workers.
        pool.shutdown(wait=True, cancel_futures=True)
        if pending_commit:
            conn.commit()
        raise
    else:
        if pending_commit:
            conn.commit()
    return hashed, failed


def add_perceptual_hashes(conn: sqlite3.Connection, workers: int = 0) -> tuple[int, int]:
    """
    Compute perceptual_hash (phash hash_size=16, 64 hex chars) for images where it is NULL.

    Used for exact duplicate matching in find_unified.
    Movies and iCloud-only photos are skipped.

    Args:
        workers: Parallel threads (0 = auto, capped at 8).
    Returns:
        (hashed, failed) counts.
    """
    try:
        import imagehash
        import pillow_heif
        from PIL import ImageFile
        pillow_heif.register_heif_opener()
        ImageFile.LOAD_TRUNCATED_IMAGES = True
    except ImportError as e:
        raise ImportError(f"Required: pip install imagehash Pillow pillow-heif  —  {e}")

    pending = _pending_rows(conn, "perceptual_hash")
    inaccessible = conn.execute("""
        SELECT COUNT(*) FROM photos
        WHERE perceptual_hash IS NULL
          AND source_file NOT LIKE 'macphotos://%'
          AND UPPER(COALESCE(file_type,'')) NOT IN ({})
    """.format(", ".join(f"'{t}'" for t in _MOVIE_TYPES))).fetchone()[0] - len(pending)

    if not pending:
        return 0, inaccessible

    n_workers = workers if workers > 0 else min(os.cpu_count() or 4, 8)
    label = f"Hashing {len(pending)} images ({n_workers} threads)"
    hashed, failed = _run_hash_pool(
        pending, n_workers,
        hash_fn=lambda img: str(imagehash.phash(img, hash_size=16)),
        update_sql="UPDATE photos SET perceptual_hash = ? WHERE id = ?",
        conn=conn,
        label=label,
    )
    return hashed, failed + inaccessible


def add_fuzzy_hashes(conn: sqlite3.Connection, workers: int = 0) -> tuple[int, int]:
    """
    Compute perceptual_hash_small (phash hash_size=8, 16 hex chars) for images where it is NULL.

    Used for fuzzy Hamming-distance matching in find_unified (signal 4).
    Movies and iCloud-only photos are skipped.

    Args:
        workers: Parallel threads (0 = auto, capped at 8).
    Returns:
        (hashed, failed) counts.
    """
    try:
        import imagehash
        import pillow_heif
        from PIL import ImageFile
        pillow_heif.register_heif_opener()
        ImageFile.LOAD_TRUNCATED_IMAGES = True
    except ImportError as e:
        raise ImportError(f"Required: pip install imagehash Pillow pillow-heif  —  {e}")

    pending = _pending_rows(conn, "perceptual_hash_small")
    inaccessible = conn.execute("""
        SELECT COUNT(*) FROM photos
        WHERE perceptual_hash_small IS NULL
          AND source_file NOT LIKE 'macphotos://%'
          AND UPPER(COALESCE(file_type,'')) NOT IN ({})
    """.format(", ".join(f"'{t}'" for t in _MOVIE_TYPES))).fetchone()[0] - len(pending)

    if not pending:
        return 0, inaccessible

    n_workers = workers if workers > 0 else min(os.cpu_count() or 4, 8)
    label = f"Fuzzy-hashing {len(pending)} images ({n_workers} threads)"
    hashed, failed = _run_hash_pool(
        pending, n_workers,
        hash_fn=lambda img: str(imagehash.phash(img, hash_size=8)),
        update_sql="UPDATE photos SET perceptual_hash_small = ? WHERE id = ?",
        conn=conn,
        label=label,
    )
    return hashed, failed + inaccessible


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
    if not pending:
        return 0, 0

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
    import sys
    from .db import connect, init_db

    parser = argparse.ArgumentParser(description="Compute perceptual hashes for a catalog.")
    parser.add_argument("--db", required=True, help="Path to the SQLite catalog database file.")
    parser.add_argument(
        "--workers", type=int, default=0,
        help="Hash threads (0 = auto, default: min(cpu_count, 8))."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear existing hashes for the selected mode and recompute from scratch."
    )
    parser.add_argument(
        "--fuzzy", action="store_true",
        help="Compute perceptual_hash_small (hash_size=8) for fuzzy matching instead of the normal hash."
    )
    args = parser.parse_args()

    conn = connect(args.db)
    init_db(conn)
    try:
        if args.fuzzy:
            if args.reset:
                conn.execute("UPDATE photos SET perceptual_hash_small = NULL")
                conn.commit()
                print("Reset: cleared all fuzzy hashes.")
            hashed, failed = add_fuzzy_hashes(conn, workers=args.workers)
            print(f"Fuzzy-hashed : {hashed}")
        else:
            if args.reset:
                conn.execute("UPDATE photos SET perceptual_hash = NULL")
                conn.commit()
                print("Reset: cleared all perceptual hashes.")
            hashed, failed = add_perceptual_hashes(conn, workers=args.workers)
            print(f"Hashed : {hashed}")
        if failed:
            print(f"Failed : {failed} (unreadable files)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
