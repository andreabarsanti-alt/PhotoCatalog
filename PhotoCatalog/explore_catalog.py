import json
import subprocess
import platform
import pandas as pd
import os

def open_files_by_key(input_json, key_name, key_value, source_key="SourceFile", app=None):
    """
    Open all files in the catalog that have a specific key-value pair.
    Warns before opening more than 2 files.

    Args:
        input_json: Path to the input JSON file
        key_name: Name of the key to filter by (e.g., "matchID")
        key_value: Value to match (e.g., 5)
        source_key: Key containing the file path to open (default: "SourceFile")
        app: Optional application to open files with (e.g., "Preview" on macOS)
    """
    try:
        # Read JSON file
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")

        # Find matching items
        matching_items = []
        for item in data:
            if key_name in item and item[key_name] == key_value:
                if source_key in item and item[source_key]:
                    matching_items.append(item[source_key])

        if len(matching_items) == 0:
            print(f"No items found with {key_name}={key_value}")
            return

        print(f"\nFound {len(matching_items)} file(s) with {key_name}={key_value}:")
        for i, path in enumerate(matching_items, 1):
            print(f"  {i}. {path}")

        # Warn if more than 2 files
        if len(matching_items) > 2:
            print(f"\n⚠️  Warning: About to open {len(matching_items)} files!")
            response = input("Do you want to continue? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("Cancelled.")
                return

        # Open files
        print(f"\nOpening {len(matching_items)} file(s)...")
        opened_count = 0
        error_count = 0

        for file_path in matching_items:
            try:
                # Detect OS and open file with default application or specified app
                system = platform.system()

                if system == 'Darwin':  # macOS
                    if app:
                        # Open with specific application
                        subprocess.run(['open', '-a', app, file_path], check=True)
                    else:
                        # Try to open with default app, force Preview for HEIC files if default fails
                        try:
                            subprocess.run(['open', file_path], check=True)
                        except subprocess.CalledProcessError:
                            # If default fails (common with HEIC), try Preview
                            print(f"  Default app failed, trying Preview for: {file_path}")
                            subprocess.run(['open', '-a', 'Preview', file_path], check=True)

                elif system == 'Windows':
                    if app:
                        subprocess.run([app, file_path], check=True)
                    else:
                        subprocess.run(['start', '', file_path], shell=True, check=True)

                elif system == 'Linux':
                    if app:
                        subprocess.run([app, file_path], check=True)
                    else:
                        subprocess.run(['xdg-open', file_path], check=True)
                else:
                    print(f"  Unsupported OS: {system}")
                    error_count += 1
                    continue

                opened_count += 1
                print(f"  ✓ Opened: {file_path}")

            except subprocess.CalledProcessError as e:
                error_count += 1
                print(f"  ✗ Error opening {file_path}: {e}")
            except FileNotFoundError:
                error_count += 1
                print(f"  ✗ File not found: {file_path}")

        # Statistics
        print(f"\n{'=' * 60}")
        print("RESULTS")
        print(f"{'=' * 60}")
        print(f"Successfully opened: {opened_count} file(s)")
        print(f"Errors: {error_count} file(s)")

    except FileNotFoundError:
        print(f"Error: Catalog file not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")
        raise

def compare_catalogs(catalog1, catalog2, main_key="SourceFile",
                     save_unique1=None, save_unique2=None):
    """
    Compare two catalogs and identify unique items in each.

    Args:
        catalog1: Path to the first catalog
        catalog2: Path to the second catalog
        main_key: Key to use for matching items between catalogs (default: "SourceFile")
        save_unique1: Optional path to save items unique to catalog1
        save_unique2: Optional path to save items unique to catalog2
    """
    try:
        # Read both catalogs
        print(f"Reading catalog 1: {catalog1}...")
        with open(catalog1, 'r', encoding='utf-8') as f:
            data1 = json.load(f)

        print(f"Reading catalog 2: {catalog2}...")
        with open(catalog2, 'r', encoding='utf-8') as f:
            data2 = json.load(f)

        print(f"\nCatalog 1: {len(data1)} items")
        print(f"Catalog 2: {len(data2)} items")
        print(f"Comparing on key: '{main_key}'")

        # Extract sets of main_key values
        print("\nIndexing catalogs...")
        keys1 = set()
        items1_by_key = {}
        for item in data1:
            if main_key in item and item[main_key] is not None:
                key_val = item[main_key]
                keys1.add(key_val)
                if key_val not in items1_by_key:
                    items1_by_key[key_val] = []
                items1_by_key[key_val].append(item)

        keys2 = set()
        items2_by_key = {}
        for item in data2:
            if main_key in item and item[main_key] is not None:
                key_val = item[main_key]
                keys2.add(key_val)
                if key_val not in items2_by_key:
                    items2_by_key[key_val] = []
                items2_by_key[key_val].append(item)

        print(f"Catalog 1: {len(keys1)} unique {main_key} values")
        print(f"Catalog 2: {len(keys2)} unique {main_key} values")

        # Find unique and common items
        unique_to_1 = keys1 - keys2
        unique_to_2 = keys2 - keys1
        common = keys1 & keys2

        # Print statistics
        print(f"\n{'=' * 60}")
        print("COMPARISON RESULTS")
        print(f"{'=' * 60}")
        print(f"Items in both catalogs: {len(common)}")
        print(f"Items only in catalog 1: {len(unique_to_1)}")
        print(f"Items only in catalog 2: {len(unique_to_2)}")

        # Show samples
        if unique_to_1:
            print(f"\nSample items unique to catalog 1 (showing first 5):")
            for key_val in list(unique_to_1)[:5]:
                print(f"  - {key_val}")
            if len(unique_to_1) > 5:
                print(f"  ... and {len(unique_to_1) - 5} more")

        if unique_to_2:
            print(f"\nSample items unique to catalog 2 (showing first 5):")
            for key_val in list(unique_to_2)[:5]:
                print(f"  - {key_val}")
            if len(unique_to_2) > 5:
                print(f"  ... and {len(unique_to_2) - 5} more")

        # Save unique items if requested
        if save_unique1 and unique_to_1:
            unique1_items = []
            for key_val in unique_to_1:
                unique1_items.extend(items1_by_key[key_val])

            print(f"\nSaving {len(unique1_items)} items unique to catalog 1...")
            with open(save_unique1, 'w', encoding='utf-8') as f:
                json.dump(unique1_items, f, indent=2)
            print(f"Saved to: {save_unique1}")

        if save_unique2 and unique_to_2:
            unique2_items = []
            for key_val in unique_to_2:
                unique2_items.extend(items2_by_key[key_val])

            print(f"\nSaving {len(unique2_items)} items unique to catalog 2...")
            with open(save_unique2, 'w', encoding='utf-8') as f:
                json.dump(unique2_items, f, indent=2)
            print(f"Saved to: {save_unique2}")

        print("\nDone!")

        # Return statistics
        return {
            'total_catalog1': len(data1),
            'total_catalog2': len(data2),
            'unique_keys_catalog1': len(keys1),
            'unique_keys_catalog2': len(keys2),
            'common': len(common),
            'unique_to_catalog1': len(unique_to_1),
            'unique_to_catalog2': len(unique_to_2)
        }

    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format - {e}")
    except Exception as e:
        print(f"Error: {e}")
        raise

def get_catalog_statistics(input_json, group_by_keys=None, compare_json=None, show_all_keys=True):
    """
    Get statistics about the catalog, optionally compare with another catalog.

    Args:
        input_json: Path to the input JSON file
        group_by_keys: List of keys to count unique values for (e.g., ["FileType", "Directory"])
        compare_json: Path to a second catalog to compare (default: None)
        show_all_keys: If True, show all keys in the catalog (default: True)
    """
    try:
        # Read JSON file
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        df = pd.DataFrame(data)

        # If comparison catalog provided, read it
        if compare_json:
            print(f"Reading comparison catalog {compare_json}...")
            with open(compare_json, 'r', encoding='utf-8') as f:
                data2 = json.load(f)
            df2 = pd.DataFrame(data2)

            print(f"\n{'=' * 60}")
            print("CATALOG COMPARISON")
            print(f"{'=' * 60}")

            # Compare totals
            print(f"\nTotal items:")
            print(f"  Catalog 1: {len(df)}")
            print(f"  Catalog 2: {len(df2)}")
            print(f"  Difference: {len(df) - len(df2):+d}")

            # Compare unique keys
            all_keys1 = set()
            for item in data:
                all_keys1.update(item.keys())

            all_keys2 = set()
            for item in data2:
                all_keys2.update(item.keys())

            print(f"\nTotal unique keys:")
            print(f"  Catalog 1: {len(all_keys1)}")
            print(f"  Catalog 2: {len(all_keys2)}")
            print(f"  Difference: {len(all_keys1) - len(all_keys2):+d}")

            # Keys only in catalog 1
            only_in_1 = all_keys1 - all_keys2
            if only_in_1:
                print(f"\nKeys only in Catalog 1: {sorted(only_in_1)}")

            # Keys only in catalog 2
            only_in_2 = all_keys2 - all_keys1
            if only_in_2:
                print(f"\nKeys only in Catalog 2: {sorted(only_in_2)}")

            # Keys in both - show differences in coverage
            if show_all_keys:
                common_keys = all_keys1 & all_keys2
                if common_keys:
                    print(f"\n{'=' * 60}")
                    print("COMMON KEYS - COVERAGE DIFFERENCES")
                    print(f"{'=' * 60}")

                    for key in sorted(common_keys):
                        count1 = sum(1 for item in data if key in item and item[key] is not None)
                        count2 = sum(1 for item in data2 if key in item and item[key] is not None)
                        pct1 = count1 / len(data) * 100 if len(data) > 0 else 0
                        pct2 = count2 / len(data2) * 100 if len(data2) > 0 else 0
                        diff = count1 - count2

                        # Only show if there's a difference
                        if diff != 0 or abs(pct1 - pct2) > 0.1:
                            print(f"\n{key}:")
                            print(f"  Catalog 1: {count1}/{len(data)} ({pct1:.1f}%)")
                            print(f"  Catalog 2: {count2}/{len(data2)} ({pct2:.1f}%)")
                            print(f"  Difference: {diff:+d}")

            # Compare specific keys if provided
            if group_by_keys:
                print(f"\n{'=' * 60}")
                print("GROUP BY KEY COMPARISON")
                print(f"{'=' * 60}")

                for key in group_by_keys:
                    in_df1 = key in df.columns
                    in_df2 = key in df2.columns

                    if not in_df1 and not in_df2:
                        print(f"\nKey '{key}': NOT FOUND in either catalog")
                        continue
                    elif not in_df1:
                        print(f"\nKey '{key}': Only in Catalog 2")
                        continue
                    elif not in_df2:
                        print(f"\nKey '{key}': Only in Catalog 1")
                        continue

                    print(f"\nKey: '{key}'")

                    # Unique values comparison
                    unique1 = df[key].nunique()
                    unique2 = df2[key].nunique()
                    print(f"  Unique values:")
                    print(f"    Catalog 1: {unique1}")
                    print(f"    Catalog 2: {unique2}")
                    print(f"    Difference: {unique1 - unique2:+d}")

                    # Non-null count comparison
                    non_null1 = df[key].notna().sum()
                    non_null2 = df2[key].notna().sum()
                    print(f"  Non-null items:")
                    print(f"    Catalog 1: {non_null1}")
                    print(f"    Catalog 2: {non_null2}")
                    print(f"    Difference: {non_null1 - non_null2:+d}")

                    # Value distribution comparison
                    if non_null1 > 0 or non_null2 > 0:
                        value_counts1 = df[key].value_counts()
                        value_counts2 = df2[key].value_counts()

                        # Get all unique values from both
                        all_values = set(value_counts1.index) | set(value_counts2.index)

                        # Show top differences
                        differences = []
                        for value in all_values:
                            count1 = value_counts1.get(value, 0)
                            count2 = value_counts2.get(value, 0)
                            if count1 != count2:
                                differences.append((value, count1, count2, abs(count1 - count2)))

                        if differences:
                            # Sort by absolute difference
                            differences.sort(key=lambda x: x[3], reverse=True)
                            print(f"  Top differences in values:")
                            for value, count1, count2, _ in differences[:10]:
                                print(f"    {value}: {count1} vs {count2} (diff: {count1 - count2:+d})")

            print(f"\n{'=' * 60}")
            return

        # Original single catalog statistics
        print(f"\n{'=' * 60}")
        print("CATALOG STATISTICS")
        print(f"{'=' * 60}")

        # Total number of items
        print(f"\nTotal items: {len(df)}")

        # Total number of unique keys across all items
        all_keys = set()
        for item in data:
            all_keys.update(item.keys())
        print(f"Total unique keys: {len(all_keys)}")

        # Show all unique keys
        if show_all_keys:
            print(f"\nAll keys found:")
            for key in sorted(all_keys):
                non_null = sum(1 for item in data if key in item and item[key] is not None)
                print(f"  {key}: {non_null}/{len(data)} items ({non_null / len(data) * 100:.1f}%)")

        # Statistics for specific keys
        if group_by_keys:
            print(f"\n{'=' * 60}")
            print("GROUP BY KEY STATISTICS")
            print(f"{'=' * 60}")

            for key in group_by_keys:
                if key not in df.columns:
                    print(f"\nKey '{key}': NOT FOUND")
                    continue

                # Count unique values
                unique_values = df[key].nunique()
                non_null_count = df[key].notna().sum()
                null_count = df[key].isna().sum()

                print(f"\nKey: '{key}'")
                print(f"  Unique values: {unique_values}")
                print(f"  Non-null items: {non_null_count}")
                print(f"  Null items: {null_count}")

                # Show value distribution (top 10)
                if non_null_count > 0:
                    value_counts = df[key].value_counts()
                    print(f"  Top values:")
                    for value, count in value_counts.head(10).items():
                        print(f"    {value}: {count} ({count / len(df) * 100:.1f}%)")

                    if len(value_counts) > 10:
                        print(f"    ... and {len(value_counts) - 10} more")

        print(f"\n{'=' * 60}")

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")
        raise
        raise

def check_image_files(json_path):
    """
    Read a JSON file and check if each SourceFile path exists on disk.

    Args:
        json_path: Path to the JSON file
    """
    # Read the JSON file
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: JSON file not found at {json_path}")
        return
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {json_path}")
        return

    # Handle both list and dict formats
    items = data if isinstance(data, list) else [data]

    # Track statistics
    total = 0
    found = 0
    missing = 0
    missing_files = []

    # Check each item
    for i, item in enumerate(items):
        if isinstance(item, dict) and 'SourceFile' in item:
            total += 1
            source_file = item['SourceFile']

            if os.path.exists(source_file):
                found += 1
                print(f"✓ Found: {source_file}")
            else:
                missing += 1
                missing_files.append(source_file)
                print(f"✗ Missing: {source_file}")

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total files checked: {total}")
    print(f"Files found: {found}")
    print(f"Files missing: {missing}")

    if missing_files:
        print("\nMissing files:")
        for f in missing_files:
            print(f"  - {f}")