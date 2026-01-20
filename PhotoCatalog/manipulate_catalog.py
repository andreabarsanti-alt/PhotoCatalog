import json
import pandas as pd
import math
from typing import Any, Optional


def _clean_item(item):
    """Remove keys with None or NaN values from an item."""
    return {k: v for k, v in item.items()
            if v is not None and not (isinstance(v, float) and math.isnan(v))}


def _clean_catalog(data):
    """Remove None/NaN values from all items in a catalog."""
    return [_clean_item(item) for item in data]


def extract_items(input_json, output_json=None,
                  filter_keys=None, filter_keys_value=None,
                  filter_string_keys=None, filter_string_keys_value=None,
                  filter_string_mode=None):
    """
    Extract items matching filter criteria into a new catalog.

    Args:
        input_json: Path to the input JSON file
        output_json: Path to save the extracted items (optional - if None, only returns data)
        filter_keys: List of keys to filter on (e.g., ["FileType"])
        filter_keys_value: List of lists of allowed values (e.g., [["JPEG", "PNG"]])
        filter_string_keys: List of string keys to filter on (e.g., ["Directory"])
        filter_string_keys_value: List of lists of string patterns (e.g., [["/photos", "/images"]])
        filter_string_mode: List of modes - "exact", "contains", "excludes" (default: ["exact"])

    Returns:
        list of extracted items
    """
    try:
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")

        filtered_data = apply_filters(
            data,
            filter_keys=filter_keys,
            filter_keys_value=filter_keys_value,
            filter_string_keys=filter_string_keys,
            filter_string_keys_value=filter_string_keys_value,
            filter_string_mode=filter_string_mode
        )

        print(f"\n{'=' * 60}")
        print("RESULTS")
        print(f"{'=' * 60}")
        print(f"Total items: {len(data)}")
        print(f"Extracted: {len(filtered_data)}")
        print(f"Excluded: {len(data) - len(filtered_data)}")

        # Save if output_json provided
        if output_json:
            print(f"\nWriting {len(filtered_data)} items to {output_json}...")
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(_clean_catalog(filtered_data), f, indent=2)
            print("Done!")

        return filtered_data

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
        return None
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        raise


def apply_filters(data, filter_keys=None, filter_keys_value=None,
                  filter_string_keys=None, filter_string_keys_value=None,
                  filter_string_mode=None, return_mask=False):
    """
    Apply filters to a list of items and return filtered list.
    Used internally by extract_items and find_images.

    Args:
        data: List of items (dicts) or pandas DataFrame
        filter_keys: List of keys to filter on (e.g., ["FileType"])
        filter_keys_value: List of lists of allowed values (e.g., [["JPEG", "PNG"]])
        filter_string_keys: List of string keys to filter on (e.g., ["Directory"])
        filter_string_keys_value: List of lists of string patterns
        filter_string_mode: List of modes - "exact", "contains", "excludes"
        return_mask: If True, return (filtered_data, mask) tuple

    Returns:
        list of filtered items, or (list, mask) tuple if return_mask=True
    """
    if isinstance(data, pd.DataFrame):
        df = data
    else:
        df = pd.DataFrame(data)

    if len(df) == 0:
        if return_mask:
            return [], pd.Series([], dtype=bool)
        return []

    process_mask = pd.Series([True] * len(df), index=df.index)

    # Apply exact value filters
    if filter_keys is not None and filter_keys_value is not None:
        if len(filter_keys) != len(filter_keys_value):
            raise ValueError("filter_keys and filter_keys_value must have the same length")

        for key, allowed_values in zip(filter_keys, filter_keys_value):
            if key not in df.columns:
                print(f"Warning: Filter key '{key}' not found, ignoring")
                continue

            key_mask = df[key].isin(allowed_values)
            process_mask = process_mask & key_mask
            print(f"Filter '{key}' in {allowed_values}: {key_mask.sum()} items match")

    # Apply string filters
    if filter_string_keys is not None and filter_string_keys_value is not None:
        if len(filter_string_keys) != len(filter_string_keys_value):
            raise ValueError("filter_string_keys and filter_string_keys_value must have the same length")

        if filter_string_mode is None:
            filter_string_mode = ["exact"] * len(filter_string_keys)
        elif len(filter_string_mode) != len(filter_string_keys):
            raise ValueError("filter_string_mode must have the same length as filter_string_keys")

        for key, patterns, mode in zip(filter_string_keys, filter_string_keys_value, filter_string_mode):
            if key not in df.columns:
                print(f"Warning: String filter key '{key}' not found, ignoring")
                continue

            if mode == "excludes":
                key_mask = pd.Series([True] * len(df), index=df.index)
            else:
                key_mask = pd.Series([False] * len(df), index=df.index)

            for pattern in patterns:
                if pattern is None or pattern == "null":
                    key_mask = key_mask | df[key].isna()
                elif mode == "exact":
                    key_mask = key_mask | (df[key] == pattern)
                elif mode == "contains":
                    key_mask = key_mask | df[key].astype(str).str.contains(pattern, case=False, na=False)
                elif mode == "excludes":
                    key_mask = key_mask & ~df[key].astype(str).str.contains(pattern, case=False, na=False)

            process_mask = process_mask & key_mask
            print(f"String filter '{key}' ({mode}) {patterns}: {key_mask.sum()} items match")

    print(f"\nTotal after filtering: {process_mask.sum()}")
    print(f"Excluded by filters: {(~process_mask).sum()}")

    if return_mask:
        return df[process_mask].to_dict('records'), process_mask
    return df[process_mask].to_dict('records')


def filter_catalog(input_json, output_json, keep_keys):
    """
    Filter catalog to keep only specified keys in each item.

    Args:
        input_json: Path to the input JSON file
        output_json: Path to save the filtered JSON file
        keep_keys: List of key names to keep (all others will be removed)
    """
    try:
        # Read JSON file into pandas DataFrame
        print(f"Reading {input_json}...")
        df = pd.read_json(input_json)

        print(f"Original catalog: {len(df)} images with {len(df.columns)} fields each")

        # Filter to keep only specified columns that exist in the DataFrame
        existing_keys = [key for key in keep_keys if key in df.columns]
        missing_keys = [key for key in keep_keys if key not in df.columns]

        if missing_keys:
            print(f"Warning: These keys were not found in the data: {missing_keys}")

        # Create filtered DataFrame with all specified keys
        # Missing keys will be filled with None/NaN
        df_filtered = pd.DataFrame()

        for key in keep_keys:
            if key in df.columns:
                df_filtered[key] = df[key]
            else:
                df_filtered[key] = None
                print(f"Note: '{key}' not found in any items, adding as null")

        # Check how many items have each key
        print("\nKey availability:")
        for key in keep_keys:
            if key in df.columns:
                non_null = df[key].notna().sum()
                print(f"  {key}: {non_null}/{len(df)} items ({non_null / len(df) * 100:.1f}%)")

        # Convert to JSON and save
        print(f"\nWriting filtered catalog to {output_json}...")
        df_filtered.to_json(output_json, orient='records', indent=2)

        print(f"\nSuccess!")
        print(f"Filtered catalog: {len(df_filtered)} images with {len(df_filtered.columns)} fields each")
        print(f"Size reduction: {len(df.columns)} → {len(df_filtered.columns)} fields")

        # Show sample of what was kept
        print(f"\nSample of filtered data:")
        print(df_filtered.head(3).to_string())

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")

def remove_keys(input_json, output_json, keys_to_remove):
    """
    Remove specified keys from all items in the catalog.

    Args:
        input_json: Path to the input JSON file
        output_json: Path to save the modified JSON file
        keys_to_remove: List of key names to remove from all items
    """
    try:
        # Read JSON file
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")

        # Track statistics
        keys_found = {key: 0 for key in keys_to_remove}

        # Remove keys from each item
        for item in data:
            for key in keys_to_remove:
                if key in item:
                    del item[key]
                    keys_found[key] += 1

        # Print statistics
        print(f"\n{'=' * 60}")
        print("RESULTS")
        print(f"{'=' * 60}")
        for key, count in keys_found.items():
            print(f"Removed '{key}' from {count} items")

        # Save modified catalog
        print(f"\nWriting modified catalog to {output_json}...")
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(_clean_catalog(data), f, indent=2)

        print("Done!")

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")


def rename_key(input_json, output_json, original_name, new_name):
    """
    Rename a key in all items of the catalog. Values stay unchanged.

    Args:
        input_json: Path to the input JSON file
        output_json: Path to save the modified JSON file
        original_name: Current name of the key to rename
        new_name: New name for the key
    """
    try:
        # Read JSON file
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")
        print(f"Renaming '{original_name}' -> '{new_name}'")

        # Track statistics
        renamed_count = 0
        missing_count = 0

        # Rename key in each item
        for item in data:
            if original_name in item:
                item[new_name] = item.pop(original_name)
                renamed_count += 1
            else:
                missing_count += 1

        # Print statistics
        print(f"\n{'=' * 60}")
        print("RESULTS")
        print(f"{'=' * 60}")
        print(f"Items with key renamed: {renamed_count}")
        print(f"Items missing original key: {missing_count}")

        # Save modified catalog
        print(f"\nWriting modified catalog to {output_json}...")
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(_clean_catalog(data), f, indent=2)

        print("Done!")

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")


def expand_catalog(input_catalog, full_catalog, output_catalog, main_key="SourceFile"):
    """
    Expand a catalog with few keys by matching items from a full catalog.

    Args:
        input_catalog: Path to the input catalog with few keys
        full_catalog: Path to the full catalog to pull complete data from
        output_catalog: Path to save the expanded catalog
        main_key: Key to use for matching items between catalogs (default: "SourceFile")
    """
    try:
        # Read both catalogs
        print(f"Reading input catalog: {input_catalog}...")
        with open(input_catalog, 'r', encoding='utf-8') as f:
            input_data = json.load(f)

        print(f"Reading full catalog: {full_catalog}...")
        with open(full_catalog, 'r', encoding='utf-8') as f:
            full_data = json.load(f)

        print(f"\nInput catalog: {len(input_data)} items")
        print(f"Full catalog: {len(full_data)} items")
        print(f"Matching on key: '{main_key}'")

        # Create index of full catalog for fast lookup
        print("\nIndexing full catalog...")
        full_index = {}
        for item in full_data:
            if main_key in item:
                key_value = item[main_key]
                if key_value not in full_index:
                    full_index[key_value] = []
                full_index[key_value].append(item)

        print(f"Indexed {len(full_index)} unique {main_key} values")

        # Expand input catalog
        print("\nExpanding catalog...")
        expanded_data = []
        stats = {
            'matched': 0,
            'not_found': 0,
            'multiple_found': 0,
            'missing_key': 0
        }

        for i, input_item in enumerate(input_data):
            if i > 0 and i % 1000 == 0:
                print(f"  Processed {i}/{len(input_data)} items...")

            # Check if main_key exists in input item
            if main_key not in input_item:
                print(f"Warning: Item {i + 1} missing '{main_key}' key, keeping original")
                expanded_data.append(input_item)
                stats['missing_key'] += 1
                continue

            key_value = input_item[main_key]

            # Look up in full catalog
            if key_value not in full_index:
                print(f"Warning: No match found for {main_key}='{key_value}', keeping original")
                expanded_data.append(input_item)
                stats['not_found'] += 1
                continue

            matches = full_index[key_value]

            if len(matches) > 1:
                print(f"Error: Multiple matches ({len(matches)}) found for {main_key}='{key_value}', using first match")
                stats['multiple_found'] += 1

            # Use the first (or only) match
            expanded_data.append(matches[0])
            stats['matched'] += 1

        # Print statistics
        print(f"\n{'=' * 60}")
        print("EXPANSION RESULTS")
        print(f"{'=' * 60}")
        print(f"Successfully matched: {stats['matched']}")
        print(f"Not found in full catalog: {stats['not_found']}")
        print(f"Multiple matches found: {stats['multiple_found']}")
        print(f"Missing main_key: {stats['missing_key']}")
        print(f"Total items in expanded catalog: {len(expanded_data)}")

        # Save expanded catalog
        print(f"\nWriting expanded catalog to {output_catalog}...")
        with open(output_catalog, 'w', encoding='utf-8') as f:
            json.dump(_clean_catalog(expanded_data), f, indent=2)

        print("Done!")

    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format - {e}")
    except Exception as e:
        print(f"Error: {e}")
        raise

def merge_catalogs(catalog_list, output_catalog, main_key="SourceFile"):
    """
    Merge multiple catalogs into one, keeping unique items based on main_key.
    In case of duplicates, the first occurrence (based on catalog order) is kept.

    Args:
        catalog_list: List of catalog file paths to merge (in priority order)
        output_catalog: Path to save the merged catalog
        main_key: Key to use for identifying unique items (default: "SourceFile")
    """
    try:
        print(f"Merging {len(catalog_list)} catalogs")
        print(f"Using '{main_key}' to identify unique items")
        print(f"Priority order (first = highest priority):")
        for i, catalog in enumerate(catalog_list, 1):
            print(f"  {i}. {catalog}")

        merged_data = []
        seen_keys = set()

        stats = {
            'total_items': 0,
            'unique_items': 0,
            'duplicates_skipped': 0,
            'missing_key': 0,
            'per_catalog': []
        }

        # Process each catalog in order
        for catalog_idx, catalog_path in enumerate(catalog_list, 1):
            print(f"\n{'=' * 60}")
            print(f"Processing catalog {catalog_idx}/{len(catalog_list)}: {catalog_path}")
            print(f"{'=' * 60}")

            # Read catalog
            with open(catalog_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            catalog_stats = {
                'path': catalog_path,
                'total': len(data),
                'added': 0,
                'duplicates': 0,
                'missing_key': 0
            }

            # Process each item
            for item in data:
                stats['total_items'] += 1

                # Check if main_key exists
                if main_key not in item or item[main_key] is None:
                    print(f"Warning: Item missing '{main_key}', skipping")
                    stats['missing_key'] += 1
                    catalog_stats['missing_key'] += 1
                    continue

                key_value = item[main_key]

                # Check if already seen
                if key_value in seen_keys:
                    stats['duplicates_skipped'] += 1
                    catalog_stats['duplicates'] += 1
                    continue

                # Add to merged catalog
                merged_data.append(item)
                seen_keys.add(key_value)
                stats['unique_items'] += 1
                catalog_stats['added'] += 1

            stats['per_catalog'].append(catalog_stats)

            print(f"Items in catalog: {catalog_stats['total']}")
            print(f"Items added: {catalog_stats['added']}")
            print(f"Duplicates skipped: {catalog_stats['duplicates']}")
            print(f"Missing key: {catalog_stats['missing_key']}")

        # Print final statistics
        print(f"\n{'=' * 60}")
        print("MERGE RESULTS")
        print(f"{'=' * 60}")
        print(f"Total items processed: {stats['total_items']}")
        print(f"Unique items in merged catalog: {stats['unique_items']}")
        print(f"Duplicates skipped: {stats['duplicates_skipped']}")
        print(f"Items with missing key: {stats['missing_key']}")

        print(f"\nPer-catalog summary:")
        for i, cat_stats in enumerate(stats['per_catalog'], 1):
            print(f"  Catalog {i}: {cat_stats['added']} added, {cat_stats['duplicates']} duplicates")

        # Save merged catalog
        print(f"\nWriting merged catalog to {output_catalog}...")
        with open(output_catalog, 'w', encoding='utf-8') as f:
            json.dump(_clean_catalog(merged_data), f, indent=2)

        print("Done!")

        return stats

    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format - {e}")
    except Exception as e:
        print(f"Error: {e}")
        raise

def clean_catalog(input_catalog, output_catalog):
    """
    Remove all keys with null/None/NaN values from all items in the catalog.

    Args:
        input_catalog: Path to the input catalog
        output_catalog: Path to save the cleaned catalog
    """

    def is_null_or_nan(value):
        """Check if a value is None, null, NaN, or empty string"""
        if value is None:
            return True
        # Check for NaN (works for both float NaN and string "NaN")
        if isinstance(value, float) and math.isnan(value):
            return True
        if isinstance(value, str) and value.lower() == 'nan':
            return True
        return False

    try:
        # Read catalog
        print(f"Reading catalog: {input_catalog}...")
        with open(input_catalog, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")

        # Track statistics
        total_keys_before = 0
        total_keys_after = 0
        null_keys_removed = 0

        # Clean each item
        print("\nCleaning items...")
        cleaned_data = []

        for i, item in enumerate(data):
            if i > 0 and i % 1000 == 0:
                print(f"  Processed {i}/{len(data)} items...")

            keys_before = len(item)
            total_keys_before += keys_before

            # Create cleaned item with only non-null/non-NaN values
            cleaned_item = {
                key: value
                for key, value in item.items()
                if not is_null_or_nan(value)
            }

            keys_after = len(cleaned_item)
            total_keys_after += keys_after
            null_keys_removed += (keys_before - keys_after)

            cleaned_data.append(cleaned_item)

        # Calculate averages
        avg_keys_before = total_keys_before / len(data) if len(data) > 0 else 0
        avg_keys_after = total_keys_after / len(data) if len(data) > 0 else 0

        # Print statistics
        print(f"\n{'=' * 60}")
        print("CLEANING RESULTS")
        print(f"{'=' * 60}")
        print(f"Total items: {len(data)}")
        print(f"Total keys before: {total_keys_before}")
        print(f"Total keys after: {total_keys_after}")
        print(f"Null/NaN keys removed: {null_keys_removed}")
        print(f"Average keys per item before: {avg_keys_before:.1f}")
        print(f"Average keys per item after: {avg_keys_after:.1f}")

        # Save cleaned catalog
        print(f"\nWriting cleaned catalog to {output_catalog}...")
        with open(output_catalog, 'w', encoding='utf-8') as f:
            json.dump(cleaned_data, f, indent=2)

        print("Done!")

        return {
            'total_items': len(data),
            'total_keys_before': total_keys_before,
            'total_keys_after': total_keys_after,
            'null_keys_removed': null_keys_removed
        }

    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format - {e}")
    except Exception as e:
        print(f"Error: {e}")
        raise

def add_key_to_catalog(
        input_file: str,
        output_file: str,
        new_key: str,
        value: Optional[Any] = None,
        source_key: Optional[str] = None,
        replace: bool = False,
        indent: int = 2
) -> None:
    """
    Add a new key to each item in a catalog JSON file and write to a new JSON file.

    Args:
        input_file: Path to the input JSON file
        output_file: Path to the output JSON file
        new_key: Name of the key to add/update
        value: Value to set for the new key (if provided, takes precedence over source_key)
        source_key: Name of existing key whose value should be copied to new_key
        replace: If True, replace existing keys. If False, skip items where key exists
        indent: Number of spaces for JSON indentation (default: 2)

    Raises:
        ValueError: If neither value nor source_key is provided
        FileNotFoundError: If input file doesn't exist
        json.JSONDecodeError: If input file is not valid JSON
    """
    if value is None and source_key is None:
        raise ValueError("Must provide either 'value' or 'source_key'")

    # Read input JSON file
    print(f"Reading input file: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        input_catalog = json.load(f)

    total_items = len(input_catalog)
    print(f"Loaded {total_items} items from catalog")

    # Determine operation mode
    if value is not None:
        print(f"Adding key '{new_key}' with value: {value}")
    else:
        print(f"Copying key '{source_key}' to '{new_key}'")

    if replace:
        print("Replace mode: ON (will overwrite existing keys)")
    else:
        print("Replace mode: OFF (will skip existing keys)")

    output_catalog = []
    items_modified = 0
    items_skipped = 0
    items_missing_source = 0

    print(f"\nProcessing items...")
    for i, item in enumerate(input_catalog, 1):
        # Create a copy of the item
        new_item = item.copy()

        # Skip adding/updating if key exists and replace is False
        if new_key in new_item and not replace:
            output_catalog.append(new_item)
            items_skipped += 1
            continue

        # Use passed value if provided, otherwise copy from source_key
        if value is not None:
            new_item[new_key] = value
            items_modified += 1
        elif source_key in new_item:
            new_item[new_key] = new_item[source_key]
            items_modified += 1
        else:
            # If source_key doesn't exist in this item, skip adding the key
            items_missing_source += 1

        output_catalog.append(new_item)

    # Print summary
    print(f"\nProcessing complete:")
    print(f"  - Items modified: {items_modified}")
    print(f"  - Items skipped (key exists): {items_skipped}")
    if source_key:
        print(f"  - Items missing source key '{source_key}': {items_missing_source}")
    print(f"  - Total items: {total_items}")

    # Write to output JSON file
    print(f"\nWriting to output file: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(_clean_catalog(output_catalog), f, indent=indent, ensure_ascii=False)

    print(f"Successfully written {len(output_catalog)} items to {output_file}")


def add_key(
        input_file: str,
        output_file: str,
        key: str,
        value: Any,
        prefilter: bool = False,
        filter_keys: Optional[list] = None,
        filter_keys_value: Optional[list] = None,
        filter_string_keys: Optional[list] = None,
        filter_string_keys_value: Optional[list] = None,
        filter_string_mode: Optional[list] = None,
        replace: bool = False,
        indent: int = 2
) -> None:
    """
    Add a new key with a value to items in a catalog. Optionally prefilter items.

    Args:
        input_file: Path to the input JSON file
        output_file: Path to the output JSON file
        key: Name of the key to add
        value: Value to set for the key
        prefilter: If True, only add key to items matching filter criteria
        filter_keys: List of keys to filter on (e.g., ["FileType"])
        filter_keys_value: List of lists of allowed values (e.g., [["JPEG", "PNG"]])
        filter_string_keys: List of string keys to filter on (e.g., ["Directory"])
        filter_string_keys_value: List of lists of string patterns
        filter_string_mode: List of modes - "exact", "contains", "excludes" (default: ["exact"])
        replace: If True, replace existing keys. If False, skip items where key exists
        indent: Number of spaces for JSON indentation (default: 2)
    """
    # Read input JSON file
    print(f"Reading input file: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total_items = len(data)
    print(f"Loaded {total_items} items from catalog")
    print(f"Adding key '{key}' with value: {value}")

    if prefilter:
        print("\nPrefilter enabled - applying filters to select items...")
        # Get mask of items that match the filter
        _, mask = apply_filters(
            data,
            filter_keys=filter_keys,
            filter_keys_value=filter_keys_value,
            filter_string_keys=filter_string_keys,
            filter_string_keys_value=filter_string_keys_value,
            filter_string_mode=filter_string_mode,
            return_mask=True
        )
        filter_indices = set(mask[mask].index.tolist())
        print(f"\nWill add key to {len(filter_indices)} items matching filter")
    else:
        filter_indices = None

    output_catalog = []
    items_modified = 0
    items_skipped_exists = 0
    items_skipped_filter = 0

    for i, item in enumerate(data):
        new_item = item.copy()

        # Check if item passes prefilter (if enabled)
        if prefilter and i not in filter_indices:
            output_catalog.append(new_item)
            items_skipped_filter += 1
            continue

        # Skip if key exists and replace is False
        if key in new_item and not replace:
            output_catalog.append(new_item)
            items_skipped_exists += 1
            continue

        new_item[key] = value
        items_modified += 1
        output_catalog.append(new_item)

    # Print summary
    print(f"\n{'=' * 60}")
    print("RESULTS")
    print(f"{'=' * 60}")
    print(f"Items modified: {items_modified}")
    print(f"Items skipped (key exists): {items_skipped_exists}")
    if prefilter:
        print(f"Items skipped (filter): {items_skipped_filter}")
    print(f"Total items: {total_items}")

    # Write to output JSON file
    print(f"\nWriting to output file: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(_clean_catalog(output_catalog), f, indent=indent, ensure_ascii=False)

    print(f"Successfully written {len(output_catalog)} items to {output_file}")
