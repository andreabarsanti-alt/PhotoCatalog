import json
import os
import shutil
from datetime import datetime
from typing import List, Optional, Tuple


def parse_date(date_value: str) -> Tuple[str, str, str]:
    """
    Parse date from various formats and return (year, month, day).

    Supported formats:
        - "2022:03:03 15:21:38"
        - "2026:01:06 20:24:13+01:00"
        - "2022:03:03"

    Args:
        date_value: Date string to parse

    Returns:
        Tuple of (year, month, day) as strings (month/day zero-padded)

    Raises:
        ValueError: If date format is not recognized
    """
    # Handle quoted strings by stripping quotes
    if isinstance(date_value, str):
        date_value = date_value.strip('"\'')

    # List of formats to try
    formats = [
        "%Y:%m:%d %H:%M:%S%z",      # 2026:01:06 20:24:13+01:00
        "%Y:%m:%d %H:%M:%S",        # 2022:03:03 15:21:38
        "%Y:%m:%d",                 # 2022:03:03
    ]

    # For timezone format, we need to handle the colon in +01:00
    # Python 3.7+ handles this, but let's also try without colon
    date_value_no_tz_colon = date_value
    if len(date_value) > 19 and date_value[-3] == ':':
        # Convert +01:00 to +0100
        date_value_no_tz_colon = date_value[:-3] + date_value[-2:]

    for fmt in formats:
        for val in [date_value, date_value_no_tz_colon]:
            try:
                parsed_date = datetime.strptime(val, fmt)
                year = str(parsed_date.year)
                month = f"{parsed_date.month:02d}"
                day = f"{parsed_date.day:02d}"
                return year, month, day
            except ValueError:
                continue

    raise ValueError(f"Unable to parse date: {date_value}")


def organize_by_date(
        input_catalog: str,
        root_folder: str,
        date_keys: List[str],
        dry_run: bool = False,
        output_catalog: Optional[str] = None,
        log_file: Optional[str] = None
) -> None:
    """
    Organize files by date into a year/month/day folder structure.

    Args:
        input_catalog: Path to the input JSON catalog file
        root_folder: Root folder where files will be organized (files go to root_folder/year/month/day)
        date_keys: List of key names to try for extracting the date (in priority order)
        dry_run: If True, only print what would be done without moving files
        output_catalog: Optional path to write updated JSON catalog with new paths
        log_file: Optional path to write log output to a file

    Supported date formats:
        - "2022:03:03 15:21:38"
        - "2026:01:06 20:24:13+01:00"
        - "2022:03:03"
    """
    # Set up logging
    log_handle = None
    if log_file:
        log_handle = open(log_file, 'w', encoding='utf-8')

    def log(message: str = ""):
        print(message)
        if log_handle:
            log_handle.write(message + "\n")

    # Read input catalog
    log(f"Reading catalog: {input_catalog}...")
    with open(input_catalog, 'r', encoding='utf-8') as f:
        input_json = json.load(f)
    log(f"Loaded {len(input_json)} items")
    log()

    if dry_run:
        log("=" * 60)
        log("DRY RUN MODE - No files will be moved")
        log("=" * 60)
        log()
    else:
        # Ensure root folder exists
        os.makedirs(root_folder, exist_ok=True)

    stats = {
        'total_items': len(input_json),
        'moved': 0,
        'moved_no_date': 0,
        'skipped_missing_source': 0,
        'skipped_file_not_found': 0,
        'skipped_already_exists': 0,
        'errors': 0,
        'error_details': []
    }

    # Store updated items for catalog output
    updated_items = []

    log(f"Organizing {len(input_json)} files by date")
    log(f"Root folder: {root_folder}")
    log(f"Date keys (priority order): {date_keys}")
    if output_catalog:
        log(f"Output catalog: {output_catalog}")
    log()

    for i, item in enumerate(input_json):
        if i > 0 and i % 100 == 0:
            log(f"  Processed {i}/{len(input_json)} items...")

        # Create a copy of the item for potential updates
        updated_item = item.copy()

        # Check for SourceFile key
        if 'SourceFile' not in item or not item['SourceFile']:
            stats['skipped_missing_source'] += 1
            updated_items.append(updated_item)
            continue

        source_file = item['SourceFile']

        # Try each date key in priority order
        year, month, day = None, None, None
        for date_key in date_keys:
            if date_key not in item or not item[date_key]:
                continue
            try:
                year, month, day = parse_date(item[date_key])
                break  # Successfully parsed, stop trying other keys
            except (ValueError, TypeError):
                continue  # Try next key

        # If no valid date found from any key, use NoDate folder
        if year is None:
            dest_folder = os.path.join(root_folder, "NoDate")
        else:
            dest_folder = os.path.join(root_folder, year, month, day)

        # Check if source file exists (skip in dry run for files that might not exist yet)
        if not dry_run and not os.path.isfile(source_file):
            stats['skipped_file_not_found'] += 1
            updated_items.append(updated_item)
            continue

        # Build destination path
        filename = os.path.basename(source_file)
        dest_path = os.path.join(dest_folder, filename)

        # Check if destination already exists
        if not dry_run and os.path.exists(dest_path):
            stats['skipped_already_exists'] += 1
            updated_items.append(updated_item)
            continue

        if dry_run:
            log(f"  Would move: {source_file}")
            log(f"         to: {dest_path}")
            if year is None:
                stats['moved_no_date'] += 1
            else:
                stats['moved'] += 1
            # Update item with new paths
            updated_item['SourceFile'] = dest_path
            updated_item['Directory'] = dest_folder
        else:
            # Create destination folder and move file
            try:
                os.makedirs(dest_folder, exist_ok=True)
                shutil.move(source_file, dest_path)
                if year is None:
                    stats['moved_no_date'] += 1
                else:
                    stats['moved'] += 1
                # Update item with new paths
                updated_item['SourceFile'] = dest_path
                updated_item['Directory'] = dest_folder
            except Exception as e:
                stats['errors'] += 1
                stats['error_details'].append(f"Error moving {source_file} to {dest_path}: {e}")

        updated_items.append(updated_item)

    # Print summary
    log()
    log("=" * 60)
    if dry_run:
        log("DRY RUN RESULTS (no files were moved)")
    else:
        log("ORGANIZATION RESULTS")
    log("=" * 60)
    log(f"Total items: {stats['total_items']}")
    log(f"Files {'would be ' if dry_run else ''}moved: {stats['moved']}")
    log(f"Files {'would be ' if dry_run else ''}moved to NoDate: {stats['moved_no_date']}")
    log(f"Skipped (missing SourceFile): {stats['skipped_missing_source']}")
    if not dry_run:
        log(f"Skipped (file not found): {stats['skipped_file_not_found']}")
        log(f"Skipped (already exists): {stats['skipped_already_exists']}")
        log(f"Errors: {stats['errors']}")

    if stats['error_details']:
        log()
        log("Error details (first 10):")
        for detail in stats['error_details'][:10]:
            log(f"  - {detail}")

    # Write updated catalog if requested
    if output_catalog:
        log()
        log(f"Writing updated catalog to {output_catalog}...")
        with open(output_catalog, 'w', encoding='utf-8') as f:
            json.dump(updated_items, f, indent=2, ensure_ascii=False)
        log(f"Catalog written with {len(updated_items)} items")

    # Close log file if opened
    if log_handle:
        log_handle.close()
