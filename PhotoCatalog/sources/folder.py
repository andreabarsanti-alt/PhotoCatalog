"""Ingest from a disk folder using pure-Python EXIF extraction (exifread + Pillow)."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import exifread
from PIL import Image

from ..db import insert_photos

# Register HEIF/HEIC opener so Pillow can open those files
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
    ".heic", ".heif",
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf",
    ".pef", ".srw", ".x3f", ".3fr",
    ".mov", ".mp4", ".m4v",
}

FILE_TYPE_MAP = {
    ".jpg": "JPEG", ".jpeg": "JPEG",
    ".png": "PNG",
    ".tif": "TIFF", ".tiff": "TIFF",
    ".heic": "HEIC", ".heif": "HEIF",
    ".cr2": "CR2", ".cr3": "CR3",
    ".nef": "NEF", ".arw": "ARW", ".dng": "DNG",
    ".orf": "ORF", ".rw2": "RW2", ".raf": "RAF",
    ".pef": "PEF", ".srw": "SRW",
    ".mov": "MOV", ".mp4": "MP4", ".m4v": "M4V",
}


def _tag_str(tags: dict, key: str) -> Optional[str]:
    val = tags.get(key)
    return str(val).strip() if val is not None else None


def _tag_float(tags: dict, key: str) -> Optional[float]:
    val = tags.get(key)
    if val is None:
        return None
    try:
        # exifread returns IfdTag; .values is a list of Ratio objects
        v = val.values[0]
        return float(v.num) / float(v.den) if hasattr(v, "num") else float(v)
    except Exception:
        return None


def _tag_int(tags: dict, key: str) -> Optional[int]:
    val = tags.get(key)
    if val is None:
        return None
    try:
        v = val.values[0]
        return int(v.num // v.den) if hasattr(v, "num") else int(v)
    except Exception:
        return None


def _gps_decimal(tags: dict, coord_key: str, ref_key: str) -> Optional[float]:
    coord = tags.get(coord_key)
    ref = tags.get(ref_key)
    if coord is None:
        return None
    try:
        vals = coord.values  # [deg, min, sec] as Ratio objects
        deg = float(vals[0].num) / float(vals[0].den)
        mn  = float(vals[1].num) / float(vals[1].den)
        sec = float(vals[2].num) / float(vals[2].den)
        decimal = deg + mn / 60 + sec / 3600
        if ref is not None and str(ref).strip().upper() in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None


def _exif_date(tags: dict, key: str) -> Optional[str]:
    """Parse "YYYY:MM:DD HH:MM:SS" → ISO 8601."""
    raw = _tag_str(tags, key)
    if not raw or raw.startswith("0000"):
        return None
    try:
        parts = raw.split(" ", 1)
        date_part = parts[0].replace(":", "-")
        time_part = parts[1] if len(parts) > 1 else ""
        return f"{date_part}T{time_part}" if time_part else date_part
    except Exception:
        return raw


def _stat_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _extract(path: Path) -> dict:
    """Extract metadata from a single file."""
    stat = path.stat()
    suffix = path.suffix.lower()
    row: dict = {
        "source_file":    str(path),
        "source_type":    "Folder",
        "file_name":      path.name,
        "file_type":      FILE_TYPE_MAP.get(suffix, suffix.lstrip(".").upper() or None),
        "file_size":      stat.st_size,
        "date_modified":  _stat_date(stat.st_mtime),
    }

    # EXIF via exifread (works for JPEG, TIFF, and most RAW formats)
    tags: dict = {}
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:
        pass

    row["date_original"] = _exif_date(tags, "EXIF DateTimeOriginal") or _exif_date(tags, "EXIF DateTimeDigitized")
    row["date_created"]  = _exif_date(tags, "EXIF DateTimeDigitized") or _exif_date(tags, "Image DateTime")
    row["make"]          = _tag_str(tags, "Image Make")
    row["model"]         = _tag_str(tags, "Image Model")
    row["lens_model"]    = _tag_str(tags, "EXIF LensModel")
    row["focal_length"]  = _tag_float(tags, "EXIF FocalLength")
    row["aperture"]      = _tag_float(tags, "EXIF FNumber")
    row["iso"]           = _tag_int(tags, "EXIF ISOSpeedRatings")
    row["shutter_speed"] = _tag_float(tags, "EXIF ExposureTime")
    row["latitude"]      = _gps_decimal(tags, "GPS GPSLatitude",  "GPS GPSLatitudeRef")
    row["longitude"]     = _gps_decimal(tags, "GPS GPSLongitude", "GPS GPSLongitudeRef")

    # Dimensions: prefer EXIF tags, fall back to Pillow
    w = _tag_int(tags, "EXIF ExifImageWidth") or _tag_int(tags, "Image ImageWidth")
    h = _tag_int(tags, "EXIF ExifImageLength") or _tag_int(tags, "Image ImageLength")
    if not (w and h):
        try:
            with Image.open(path) as img:
                w, h = img.size
        except Exception:
            pass
    row["image_width"]  = w
    row["image_height"] = h

    return row


def ingest(conn: sqlite3.Connection, folder_path: str) -> tuple[int, int]:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    rows = []
    for dirpath, _, filenames in os.walk(folder):
        for fname in filenames:
            if Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                try:
                    rows.append(_extract(Path(dirpath) / fname))
                except Exception:
                    pass

    for row in rows:
        row["source_catalog"] = str(folder_path)

    return insert_photos(conn, rows)
