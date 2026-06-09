"""
Build or update the photo catalog database.

Run from the repo root:
    python -m PhotoCatalog.build_catalog --source MacPhotos --db catalog.db
    python -m PhotoCatalog.build_catalog --source Lightroom --db catalog.db --path /path/to/catalog.lrcat
    python -m PhotoCatalog.build_catalog --source Folder    --db catalog.db --path /path/to/photos

Options:
    --fresh            Drop and recreate the database before ingesting.
    --download         (MacPhotos only) Download iCloud-only photos to a local folder.
    --download-dir     Where to save downloaded photos (default: <db_dir>/icloud_downloads/).
    --download-limit   Max photos to download per run (useful for batching large libraries).
"""
import argparse
import sys
from pathlib import Path

from .db import connect, init_db, record_source
from .sources import mac_photos, lightroom, folder


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or update the photo catalog SQLite database."
    )
    parser.add_argument(
        "--source", required=True, choices=["MacPhotos", "Lightroom", "Folder"],
        help="Source type to ingest."
    )
    parser.add_argument(
        "--db", required=True,
        help="Path to the SQLite catalog database file."
    )
    parser.add_argument(
        "--path",
        help="Source path: library bundle (MacPhotos), .lrcat file (Lightroom), or root folder (Folder)."
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Drop and recreate the photos table before ingesting."
    )
    parser.add_argument(
        "--download", action="store_true",
        help="(MacPhotos only) Download iCloud-only photos locally via Photos.app."
    )
    parser.add_argument(
        "--download-dir",
        help="Folder for downloaded iCloud photos. Default: <db_dir>/icloud_downloads/"
    )
    parser.add_argument(
        "--download-limit", type=int, default=None,
        help="Max number of iCloud photos to download per run (for batching)."
    )
    parser.add_argument(
        "--workers", type=int, default=0,
        help="Parallel threads for perceptual hashing (0 = auto, default: min(cpu_count, 8))."
    )
    parser.add_argument(
        "--no-hash", action="store_true",
        help="Skip perceptual hashing after ingestion."
    )
    args = parser.parse_args()

    if args.download and args.source != "MacPhotos":
        parser.error("--download is only applicable to --source MacPhotos")

    conn = connect(args.db)
    init_db(conn, drop_existing=args.fresh)

    print(f"Source : {args.source}")
    print(f"DB     : {args.db}")
    if args.path:
        print(f"Path   : {args.path}")
    print()

    try:
        if args.source == "MacPhotos":
            inserted, skipped = mac_photos.ingest(conn, library_path=args.path)
        elif args.source == "Lightroom":
            if not args.path:
                parser.error("--path is required for Lightroom (path to .lrcat file)")
            inserted, skipped = lightroom.ingest(conn, lrcat_path=args.path)
        elif args.source == "Folder":
            if not args.path:
                parser.error("--path is required for Folder source")
            inserted, skipped = folder.ingest(conn, folder_path=args.path, workers=args.workers)

        record_source(conn, args.source, args.path, inserted)

        print(f"Inserted : {inserted}")
        print(f"Skipped  : {skipped} (already in catalog)")

        if args.download:
            print()
            from .enrich import download_cloud_photos
            download_dir = args.download_dir or str(Path(args.db).parent / "icloud_downloads")
            print(f"Download dir: {download_dir}")
            ok, failed = download_cloud_photos(
                conn,
                library_path=args.path,
                download_dir=download_dir,
                limit=args.download_limit,
            )
            print(f"Downloaded : {ok}")
            if failed:
                print(f"Failed     : {failed}")

        if not args.no_hash:
            print()
            from .enrich import add_perceptual_hashes
            hashed, failed = add_perceptual_hashes(conn, workers=args.workers)
            print(f"Hashed   : {hashed}")
            if failed:
                print(f"Failed   : {failed} (unreadable files)")


    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
