import json
import os
import shutil
import math


def _clean_item(item):
    """Remove keys with None or NaN values from an item."""
    return {k: v for k, v in item.items()
            if v is not None and not (isinstance(v, float) and math.isnan(v))}


def _clean_catalog(data):
    """Remove None/NaN values from all items in a catalog."""
    return [_clean_item(item) for item in data]


def remove_files(input_json, key_name, trash_folder, output_json=None,
                 key_value=None, source_key="SourceFile", dry_run=False,
                 update_catalog=True):
    """
    Move files matching a key condition to a trash folder and remove them from the catalog.

    Args:
        input_json: Path to the input JSON catalog
        key_name: Name of the key to check
        trash_folder: Directory to move files to (will be created if it doesn't exist)
        output_json: Path to save the updated catalog (default: overwrites input_json)
        key_value: Value to match. If None, any item where the key exists (and is not None) will match
        source_key: Key containing the file path (default: "SourceFile")
        dry_run: If True, simulate the operation without moving files
        update_catalog: If True, save the updated catalog even during dry run (default: True)

    Returns:
        dict: Statistics about the operation
    """
    if output_json is None:
        output_json = input_json

    try:
        # Read catalog
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")

        # Create trash folder if not dry run
        if not dry_run and not os.path.exists(trash_folder):
            os.makedirs(trash_folder)
            print(f"Created trash folder: {trash_folder}")

        # Track statistics
        matched_items = []
        kept_items = []
        moved_count = 0
        already_missing = 0
        move_errors = 0

        print(f"\nSearching for items where '{key_name}'" +
              (f" = {key_value}" if key_value is not None else " exists"))
        print(f"{'[DRY RUN] ' if dry_run else ''}Moving matched files to: {trash_folder}")
        print()

        for item in data:
            # Check if item matches the condition
            matches = False
            if key_name in item:
                if key_value is None:
                    # Match if key exists and is not None
                    matches = item[key_name] is not None
                else:
                    # Match if key equals the specified value
                    matches = item[key_name] == key_value

            if matches:
                matched_items.append(item)
                source_file = item.get(source_key)

                if source_file:
                    if os.path.exists(source_file):
                        # Build destination path
                        filename = os.path.basename(source_file)
                        dest_path = os.path.join(trash_folder, filename)

                        # Handle filename conflicts
                        if os.path.exists(dest_path):
                            base, ext = os.path.splitext(filename)
                            counter = 1
                            while os.path.exists(dest_path):
                                dest_path = os.path.join(trash_folder, f"{base}_{counter}{ext}")
                                counter += 1

                        if dry_run:
                            print(f"  [DRY RUN] Would move: {source_file}")
                            print(f"            -> {dest_path}")
                            moved_count += 1
                        else:
                            try:
                                shutil.move(source_file, dest_path)
                                print(f"  Moved: {source_file}")
                                print(f"      -> {dest_path}")
                                moved_count += 1
                            except Exception as e:
                                print(f"  ERROR moving {source_file}: {e}")
                                move_errors += 1
                    else:
                        print(f"  File not found (already missing): {source_file}")
                        already_missing += 1
                else:
                    print(f"  Item has no {source_key}")
            else:
                kept_items.append(item)

        # Print summary
        print(f"\n{'=' * 60}")
        print("RESULTS")
        print(f"{'=' * 60}")
        print(f"Total items in catalog: {len(data)}")
        print(f"Items matching condition: {len(matched_items)}")
        print(f"Items kept: {len(kept_items)}")
        print(f"Files moved to trash: {moved_count}")
        print(f"Files already missing: {already_missing}")
        print(f"Move errors: {move_errors}")

        # Save updated catalog
        if update_catalog:
            print(f"\nSaving updated catalog ({len(kept_items)} items) to {output_json}...")
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(_clean_catalog(kept_items), f, indent=2)
            print("Done!")
        else:
            print(f"\nCatalog not updated (update_catalog=False)")

        return {
            'total_items': len(data),
            'matched_items': len(matched_items),
            'kept_items': len(kept_items),
            'files_moved': moved_count,
            'files_missing': already_missing,
            'move_errors': move_errors
        }

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
        return None
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        raise
