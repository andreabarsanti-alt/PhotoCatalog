"""Ingest from macOS Photos library using the osxphotos Python library."""
import json
import sqlite3
from typing import Optional

from tqdm import tqdm

from ..db import insert_photos


def ingest(conn: sqlite3.Connection, library_path: Optional[str] = None) -> tuple[int, int]:
    """
    Ingest photos from the macOS Photos library.

    Photos that are only in iCloud (no local copy) are catalogued using a
    synthetic source_file of 'macphotos://<uuid>' so their metadata is
    preserved. Perceptual hashes can only be computed once the files are
    downloaded locally.

    Args:
        conn: SQLite connection to the catalog database.
        library_path: Path to .photoslibrary bundle. Uses the default library if None.

    Returns:
        (inserted, skipped) counts.
    """
    try:
        import osxphotos
    except ImportError:
        raise ImportError("osxphotos is required: pip install osxphotos")

    kwargs = {"dbfile": library_path} if library_path else {}
    print("Loading Photos library...", end=" ", flush=True)
    db = osxphotos.PhotosDB(**kwargs)
    photos = db.photos(movies=True)  # includes both photos and movies
    print(f"{len(photos)} photos")
    catalog = str(db.db_path)

    local = 0
    cloud = 0
    inserted_total = 0
    skipped_total = 0
    batch: list[dict] = []

    for p in tqdm(photos, desc="Reading metadata", unit="photo"):
        path = p.path
        if path:
            source_file = path
            local += 1
        else:
            # Cloud-only: synthetic URI keeps the photo in the catalog
            source_file = f"macphotos://{p.uuid}"
            cloud += 1

        exif = p.exif_info

        batch.append({
            "source_file":       source_file,
            "source_type":       "MacPhotos",
            "source_catalog":    catalog,
            "file_name":         p.filename,
            "original_filename": p.original_filename,
            "file_type":         _uti_to_type(p.uti_original or p.uti),
            "file_size":         p.original_filesize,
            "image_width":       p.original_width or p.width,
            "image_height":      p.original_height or p.height,
            "date_original":     (p.date_original or p.date).isoformat() if (p.date_original or p.date) else None,
            "date_created":      p.date_added.isoformat() if p.date_added else None,
            "date_modified":     p.date_modified.isoformat() if p.date_modified else None,
            "make":              exif.camera_make if exif else None,
            "model":             exif.camera_model if exif else None,
            "lens_model":        exif.lens_model if exif else None,
            "focal_length":      exif.focal_length if exif else None,
            "aperture":          exif.aperture if exif else None,
            "iso":               exif.iso if exif else None,
            "shutter_speed":     exif.shutter_speed if exif else None,
            "latitude":          p.latitude,
            "longitude":         p.longitude,
            "caption":           p.description or None,
            "keywords":          json.dumps(p.keywords) if p.keywords else None,
            "has_adjustments":   int(p.hasadjustments) if p.hasadjustments is not None else None,
            "mp_uuid":           p.uuid,
            "mp_favorite":       int(p.favorite),
            "mp_score":          _safe_score(p),
            "mp_is_live":        int(p.live_photo),
            "mp_is_burst":       int(p.burst),
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

    print(f"  Local files: {local} | Cloud-only (macphotos://): {cloud}")
    return inserted_total, skipped_total


def _safe_score(p) -> Optional[float]:
    try:
        s = p.score
        return float(s.overall) if s is not None else None
    except Exception:
        return None


def _uti_to_type(uti: Optional[str]) -> Optional[str]:
    if not uti:
        return None
    mapping = {
        "public.jpeg":               "JPEG",
        "public.png":                "PNG",
        "public.heic":               "HEIC",
        "public.heif":               "HEIF",
        "public.tiff":               "TIFF",
        "public.mpeg-4":             "MP4",
        "com.apple.quicktime-movie": "MOV",
        "public.mpeg":               "MPEG",
        "public.avi":                "AVI",
    }
    return mapping.get(uti.lower(), uti.split(".")[-1].upper())
