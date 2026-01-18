import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple


# Apple Cocoa epoch: January 1, 2001
APPLE_EPOCH = datetime(2001, 1, 1)

DEFAULT_LIBRARY_PATH = Path.home() / "Pictures" / "Photos Library.photoslibrary"


def _convert_apple_timestamp(timestamp: Optional[float]) -> Optional[datetime]:
    """Convert Apple Cocoa timestamp to Python datetime."""
    if timestamp is None:
        return None
    return APPLE_EPOCH + timedelta(seconds=timestamp)


def get_original_filename(uuid_filename: str, library_path: Optional[str] = None) -> Optional[str]:
    """
    Get the original filename for a UUID-based filename.

    Args:
        uuid_filename: The UUID filename (e.g., "F0E5CF30-3BDD-41AA-8485-575C83193B63.jpg")
        library_path: Optional custom path to Photos library

    Returns:
        Original filename or None if not found
    """
    lib_path = Path(library_path) if library_path else DEFAULT_LIBRARY_PATH
    db_path = lib_path / "database" / "Photos.sqlite"

    if not db_path.exists():
        raise FileNotFoundError(f"Photos database not found at {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT aa.ZORIGINALFILENAME
            FROM ZASSET a
            LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
            WHERE a.ZFILENAME = ?
        """, (uuid_filename,))
        result = cursor.fetchone()
        return result[0] if result else None
    finally:
        conn.close()


def list_all_photos(library_path: Optional[str] = None) -> List[Tuple[str, str]]:
    """
    List all photos in the library.

    Args:
        library_path: Optional custom path to Photos library

    Returns:
        List of tuples (uuid_filename, original_filename)
    """
    lib_path = Path(library_path) if library_path else DEFAULT_LIBRARY_PATH
    db_path = lib_path / "database" / "Photos.sqlite"

    if not db_path.exists():
        raise FileNotFoundError(f"Photos database not found at {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT a.ZFILENAME, aa.ZORIGINALFILENAME
            FROM ZASSET a
            LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
            WHERE a.ZFILENAME IS NOT NULL
            ORDER BY a.ZDATECREATED DESC
        """)
        return cursor.fetchall()
    finally:
        conn.close()


def get_photo_metadata(uuid_filename: str, library_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get full metadata for a photo.

    Args:
        uuid_filename: The UUID filename
        library_path: Optional custom path to Photos library

    Returns:
        Dictionary with photo metadata or None if not found
    """
    lib_path = Path(library_path) if library_path else DEFAULT_LIBRARY_PATH
    db_path = lib_path / "database" / "Photos.sqlite"

    if not db_path.exists():
        raise FileNotFoundError(f"Photos database not found at {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                a.ZFILENAME,
                aa.ZORIGINALFILENAME,
                a.ZDATECREATED,
                a.ZDIRECTORY,
                a.ZLATITUDE,
                a.ZLONGITUDE,
                aa.ZORIGINALWIDTH,
                aa.ZORIGINALHEIGHT
            FROM ZASSET a
            LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
            WHERE a.ZFILENAME = ?
        """, (uuid_filename,))
        result = cursor.fetchone()

        if not result:
            return None

        return {
            'uuid_filename': result[0],
            'original_filename': result[1],
            'date_created': _convert_apple_timestamp(result[2]),
            'directory': result[3],
            'latitude': result[4],
            'longitude': result[5],
            'width': result[6],
            'height': result[7]
        }
    finally:
        conn.close()


class PhotosLibrary:
    """Advanced interface for querying Mac Photos library."""

    def __init__(self, library_path: Optional[str] = None):
        """
        Initialize PhotosLibrary.

        Args:
            library_path: Optional custom path to Photos library
        """
        self.library_path = Path(library_path) if library_path else DEFAULT_LIBRARY_PATH
        self.db_path = self.library_path / "database" / "Photos.sqlite"

        if not self.db_path.exists():
            raise FileNotFoundError(f"Photos database not found at {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get a read-only database connection."""
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def get_original_filename(self, uuid_filename: str) -> Optional[str]:
        """
        Get the original filename for a UUID-based filename.

        Args:
            uuid_filename: The UUID filename

        Returns:
            Original filename or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT aa.ZORIGINALFILENAME
                FROM ZASSET a
                LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
                WHERE a.ZFILENAME = ?
            """, (uuid_filename,))
            result = cursor.fetchone()
            return result[0] if result else None
        finally:
            conn.close()

    def list_all_photos(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        List all photos with full metadata.

        Args:
            limit: Optional limit on number of results

        Returns:
            List of dictionaries with photo metadata
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            query = """
                SELECT
                    a.ZFILENAME,
                    aa.ZORIGINALFILENAME,
                    a.ZDATECREATED,
                    a.ZDIRECTORY,
                    a.ZLATITUDE,
                    a.ZLONGITUDE,
                    aa.ZORIGINALWIDTH,
                    aa.ZORIGINALHEIGHT
                FROM ZASSET a
                LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
                WHERE a.ZFILENAME IS NOT NULL
                ORDER BY a.ZDATECREATED DESC
            """
            if limit:
                query += f" LIMIT {int(limit)}"

            cursor.execute(query)
            results = []

            for row in cursor.fetchall():
                results.append({
                    'uuid_filename': row[0],
                    'original_filename': row[1],
                    'date_created': _convert_apple_timestamp(row[2]),
                    'directory': row[3],
                    'latitude': row[4],
                    'longitude': row[5],
                    'width': row[6],
                    'height': row[7]
                })

            return results
        finally:
            conn.close()

    def search_by_original_filename(self, pattern: str) -> List[Dict[str, Any]]:
        """
        Search for photos by original filename pattern.

        Args:
            pattern: SQL LIKE pattern (e.g., "IMG_%" or "%.HEIC")

        Returns:
            List of matching photos with metadata
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    a.ZFILENAME,
                    aa.ZORIGINALFILENAME,
                    a.ZDATECREATED,
                    a.ZDIRECTORY,
                    a.ZLATITUDE,
                    a.ZLONGITUDE
                FROM ZASSET a
                LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
                WHERE aa.ZORIGINALFILENAME LIKE ?
                ORDER BY a.ZDATECREATED DESC
            """, (pattern,))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'uuid_filename': row[0],
                    'original_filename': row[1],
                    'date_created': _convert_apple_timestamp(row[2]),
                    'directory': row[3],
                    'latitude': row[4],
                    'longitude': row[5]
                })

            return results
        finally:
            conn.close()

    def get_photo_path(self, uuid_filename: str) -> Optional[Path]:
        """
        Get the full filesystem path for a photo.

        Args:
            uuid_filename: The UUID filename

        Returns:
            Path object or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT ZDIRECTORY, ZFILENAME
                FROM ZASSET
                WHERE ZFILENAME = ?
            """, (uuid_filename,))
            result = cursor.fetchone()

            if not result or not result[0]:
                return None

            directory, filename = result
            return self.library_path / "originals" / directory / filename
        finally:
            conn.close()

    def get_albums(self) -> List[Dict[str, Any]]:
        """
        Get all albums in the library.

        Returns:
            List of dictionaries with album information
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT Z_PK, ZTITLE, ZCREATIONDATE
                FROM ZGENERICALBUM
                WHERE ZTITLE IS NOT NULL
                ORDER BY ZTITLE
            """)

            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'title': row[1],
                    'date_created': _convert_apple_timestamp(row[2])
                })

            return results
        finally:
            conn.close()

    def get_photos_in_album(self, album_name: str) -> List[Dict[str, Any]]:
        """
        Get all photos in a specific album.

        Args:
            album_name: Name of the album

        Returns:
            List of photos in the album
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    a.ZFILENAME,
                    aa.ZORIGINALFILENAME,
                    a.ZDATECREATED,
                    a.ZDIRECTORY,
                    a.ZLATITUDE,
                    a.ZLONGITUDE
                FROM ZASSET a
                LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
                JOIN Z_26ASSETS za ON a.Z_PK = za.Z_34ASSETS
                JOIN ZGENERICALBUM al ON za.Z_26ALBUMS = al.Z_PK
                WHERE al.ZTITLE = ?
                ORDER BY a.ZDATECREATED DESC
            """, (album_name,))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'uuid_filename': row[0],
                    'original_filename': row[1],
                    'date_created': _convert_apple_timestamp(row[2]),
                    'directory': row[3],
                    'latitude': row[4],
                    'longitude': row[5]
                })

            return results
        finally:
            conn.close()

    def create_uuid_to_original_map(self) -> Dict[str, str]:
        """
        Create a complete mapping from UUID filenames to original filenames.

        Returns:
            Dictionary mapping UUID filename to original filename
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT a.ZFILENAME, aa.ZORIGINALFILENAME
                FROM ZASSET a
                LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON a.Z_PK = aa.ZASSET
                WHERE a.ZFILENAME IS NOT NULL AND aa.ZORIGINALFILENAME IS NOT NULL
            """)

            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

    def export_mapping_to_json(self, output_path: str) -> None:
        """
        Export UUID to original filename mapping to a JSON file.

        Args:
            output_path: Path to output JSON file
        """
        mapping = self.create_uuid_to_original_map()

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)

        print(f"Exported {len(mapping)} mappings to {output_path}")
