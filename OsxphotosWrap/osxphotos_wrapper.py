import subprocess
import sys
from pathlib import Path
from typing import Literal


def export_photos(
        library_path: str,
        target_directory: str,
        media_type: Literal["photos", "movies", "both"] = "both",
        dry_run: bool = False,
        max_files: int = None
) -> int:
    """
    Export photos/movies from a macOS Photos library using osxphotos.

    Args:
        library_path: Path to the Photos library (e.g., '~/Pictures/Photos Library.photoslibrary')
        target_directory: Directory where photos will be exported
        media_type: Type of media to export - 'photos', 'movies', or 'both' (default: 'both')
        dry_run: If True, performs a dry run without actually exporting files (default: False)
        max_files: Maximum number of photos to export; None for no limit (default: None)

    Returns:
        int: Return code from the osxphotos command (0 for success)

    Example:
        >>> export_photos(
        ...     library_path="~/Pictures/Photos Library.photoslibrary",
        ...     target_directory="~/exports",
        ...     media_type="photos",
        ...     dry_run=True,
        ...     max_files=100
        ... )
    """
    # Create target directory if it doesn't exist
    target_path = Path(target_directory).expanduser()
    target_path.mkdir(parents=True, exist_ok=True)

    # Build the osxphotos command
    cmd = ["osxphotos", "export"]

    # Add the target directory (convert to string to handle PosixPath)
    cmd.append(str(target_directory))

    # Add the library path (convert to string to handle PosixPath)
    cmd.extend(["--db", str(library_path)])

    # Add media type filters
    if media_type == "photos":
        cmd.append("--only-photos")
    elif media_type == "movies":
        cmd.append("--only-movies")
    # If 'both', don't add any filter (exports everything)

    # Add export-by-date switch
    cmd.append("--export-by-date")

    # Add limit if specified
    if max_files is not None:
        cmd.extend(["--limit", str(max_files)])

    # Add dry-run if requested
    if dry_run:
        cmd.append("--dry-run")

    cmd.append("--verbose")

    # Print the command for transparency
    print(f"Executing command: {' '.join(cmd)}")
    print("-" * 80)

    # Execute the command
    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except FileNotFoundError:
        print("Error: osxphotos command not found. Please install osxphotos first:", file=sys.stderr)
        print("  pip install osxphotos", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error executing osxphotos: {e}", file=sys.stderr)
        return 1

def library_info(library_path: str) -> int:
    """
    Get descriptive information and statistics about a Photos library.

    This function runs the osxphotos info command to display statistics about
    the Photos library including counts of photos, videos, albums, persons, etc.

    Args:
        library_path: Path to the Photos library (e.g., '~/Pictures/Photos Library.photoslibrary')

    Returns:
        int: Return code from the osxphotos command (0 for success)

    Example:
        >>> library_info("~/Pictures/Photos Library.photoslibrary")
    """
    # Build the osxphotos info command
    cmd = ["osxphotos", "info"]

    # Add the library path (convert to string to handle PosixPath)
    cmd.extend(["--db", str(library_path)])

    # Print the command for transparency
    print(f"Executing command: {' '.join(cmd)}")
    print("-" * 80)

    # Execute the command
    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except FileNotFoundError:
        print("Error: osxphotos command not found. Please install osxphotos first:", file=sys.stderr)
        print("  See https://github.com/RhetTbull/osxphotos for installation instructions", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error executing osxphotos: {e}", file=sys.stderr)
        return 1
