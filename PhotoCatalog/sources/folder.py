"""Ingest from a disk folder using exiftool for metadata extraction."""
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from ..db import insert_photos


def _normalize_date(value: Optional[str]) -> Optional[str]:
    """Convert exiftool date "YYYY:MM:DD HH:MM:SS[tz]" to ISO 8601."""
    if not value:
        return None
    try:
        parts = value.split(" ", 1)
        date_part = parts[0].replace(":", "-")
        time_part = parts[1] if len(parts) > 1 else ""
        return f"{date_part}T{time_part}" if time_part else date_part
    except Exception:
        return value


def ingest(conn: sqlite3.Connection, folder_path: str) -> tuple[int, int]:
    """
    Ingest photos from a disk folder by calling exiftool recursively.

    Args:
        conn: SQLite connection to the catalog database.
        folder_path: Root folder to scan.

    Returns:
        (inserted, skipped) counts.
    """
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    result = subprocess.run(
        [
            "exiftool",
            "-json",
            "-r",            # recursive
            "-n",            # numeric output for GPS, dimensions, etc.
            "-FileSize#",
            "-FileName",
            "-FileType",
            "-ImageWidth",
            "-ImageHeight",
            "-DateTimeOriginal",
            "-CreateDate",
            "-FileModifyDate",
            "-Make",
            "-Model",
            "-LensModel",
            "-FocalLength#",
            "-FNumber#",
            "-ISO#",
            "-ExposureTime#",
            "-GPSLatitude#",
            "-GPSLongitude#",
            str(folder),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode not in (0, 1):  # exiftool returns 1 for minor warnings
        raise RuntimeError(f"exiftool failed:\n{result.stderr}")

    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError:
        metadata = []

    rows = []
    for item in metadata:
        source_file = item.get("SourceFile")
        if not source_file:
            continue
        rows.append({
            "source_file":   source_file,
            "source_type":   "Folder",
            "source_catalog": str(folder_path),
            "file_name":     item.get("FileName"),
            "file_type":     item.get("FileType"),
            "file_size":     item.get("FileSize"),
            "image_width":   item.get("ImageWidth"),
            "image_height":  item.get("ImageHeight"),
            "date_original": _normalize_date(item.get("DateTimeOriginal") or item.get("CreateDate")),
            "date_created":  _normalize_date(item.get("CreateDate")),
            "date_modified": _normalize_date(item.get("FileModifyDate")),
            "make":          item.get("Make"),
            "model":         item.get("Model"),
            "lens_model":    item.get("LensModel"),
            "focal_length":  item.get("FocalLength"),
            "aperture":      item.get("FNumber"),
            "iso":           item.get("ISO"),
            "shutter_speed": item.get("ExposureTime"),
            "latitude":      item.get("GPSLatitude"),
            "longitude":     item.get("GPSLongitude"),
        })

    return insert_photos(conn, rows)
