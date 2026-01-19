import pandas as pd
import json
import numpy as np
import math


def _clean_item(item):
    """Remove keys with None or NaN values from an item."""
    return {k: v for k, v in item.items()
            if v is not None and not (isinstance(v, float) and math.isnan(v))}


def _clean_catalog(data):
    """Remove None/NaN values from all items in a catalog."""
    return [_clean_item(item) for item in data]


def find_images(input_json, output_json,
                filter_keys=None,
                filter_keys_value=None,
                filter_string_keys=None,
                filter_string_keys_value=None,
                filter_string_mode=None,
                exact_match_keys=None,
                string_match_keys=None,
                fuzzy_match_keys=None,
                fuzzy_tolerance=0.05,
                match_id_key="matchID"):
    """
    Find duplicate images based on metadata and add matchID.

    Args:
        input_json: Path to the input JSON file
        output_json: Path to save the JSON with match IDs
        filter_keys: List of keys to filter on (e.g., ["FileType"])
        filter_keys_value: List of lists of allowed values (e.g., [["JPEG", "PNG"]])
        filter_string_keys: List of string keys to filter on (e.g., ["Directory"])
        filter_string_keys_value: List of lists of string patterns (e.g., [["/photos", "/images"]])
        filter_string_mode: List of modes for each string key - "exact", "contains", "not_contains" (default: ["exact"] for each)
        exact_match_keys: List of numeric fields for exact matching (default: ["ImageWidth", "ImageHeight", "FileSize"])
        string_match_keys: List of string fields for exact matching (default: None)
        fuzzy_match_keys: List of numeric fields for fuzzy matching (default: None)
        fuzzy_tolerance: Tolerance for fuzzy matching (0.05 = 5% difference allowed)
        match_id_key: Name of the key to store match ID (default: "matchID")
    """

    # Set defaults
    if exact_match_keys is None:
        exact_match_keys = []
    if string_match_keys is None:
        string_match_keys = []
    if fuzzy_match_keys is None:
        fuzzy_match_keys = []

    try:
        # Read JSON file
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        df = pd.DataFrame(data)
        print(f"Loaded {len(df)} images")

        # Initialize match ID column
        df[match_id_key] = None

        # FILTERING PHASE
        print(f"\n{'=' * 60}")
        print("FILTERING PHASE")
        print(f"{'=' * 60}")

        process_mask = pd.Series([True] * len(df), index=df.index)

        # Apply exact value filters
        if filter_keys is not None and filter_keys_value is not None:
            if len(filter_keys) != len(filter_keys_value):
                raise ValueError("filter_keys and filter_keys_value must have the same length")

            for key, allowed_values in zip(filter_keys, filter_keys_value):
                if key not in df.columns:
                    print(f"Warning: Filter key '{key}' not found, ignoring this filter")
                    continue

                key_mask = df[key].isin(allowed_values)
                process_mask = process_mask & key_mask
                print(f"Filter '{key}' in {allowed_values}: {key_mask.sum()} images match")

        # Apply string filters
        if filter_string_keys is not None and filter_string_keys_value is not None:
            if len(filter_string_keys) != len(filter_string_keys_value):
                raise ValueError("filter_string_keys and filter_string_keys_value must have the same length")

            # Set default modes if not provided
            if filter_string_mode is None:
                filter_string_mode = ["exact"] * len(filter_string_keys)
            elif len(filter_string_mode) != len(filter_string_keys):
                raise ValueError("filter_string_mode must have the same length as filter_string_keys")

            for key, patterns, mode in zip(filter_string_keys, filter_string_keys_value, filter_string_mode):
                if key not in df.columns:
                    print(f"Warning: String filter key '{key}' not found, ignoring this filter")
                    continue

                key_mask = pd.Series([False] * len(df), index=df.index)

                for pattern in patterns:
                    # Handle null/None matching
                    if pattern is None or pattern == "null":
                        key_mask = key_mask | df[key].isna()
                    elif mode == "exact":
                        key_mask = key_mask | (df[key] == pattern)
                    elif mode == "contains":
                        key_mask = key_mask | df[key].astype(str).str.contains(pattern, case=False, na=False)
                    elif mode == "not_contains":
                        key_mask = key_mask | ~df[key].astype(str).str.contains(pattern, case=False, na=False)

                process_mask = process_mask & key_mask
                print(f"String filter '{key}' ({mode}) with {patterns}: {key_mask.sum()} images match")

        print(f"\nTotal images to process after filtering: {process_mask.sum()}")
        print(f"Images skipped (not matching filters): {(~process_mask).sum()}")

        # Work only with filtered images
        df_to_process = df[process_mask].copy()

        if len(df_to_process) == 0:
            print("No images to process after filtering")
            # Save catalog as-is
            result = df.to_dict('records')
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(_clean_catalog(result), f, indent=2)
            return

        # STEP 1: Exact matching
        print(f"\n{'=' * 60}")
        print("STEP 1: Exact matching")
        print(f"{'=' * 60}")

        all_match_keys = exact_match_keys + string_match_keys
        print(f"Exact match keys: {all_match_keys}")

        if not all_match_keys:
            print("Warning: No exact match keys specified")
            df_to_process['_exact_key'] = "all"
        else:
            # Validate exact match keys
            valid_rows = pd.Series([True] * len(df_to_process), index=df_to_process.index)

            # Check numeric keys (must be int/float > 0)
            for key in exact_match_keys:
                if key not in df_to_process.columns:
                    print(f"Warning: Exact match key '{key}' not found")
                    valid_rows[:] = False
                    break

                for idx, value in df_to_process[key].items():
                    if not isinstance(value, (int, float)) or value <= 0:
                        valid_rows[idx] = False

            # Check string keys (must exist and not be null)
            for key in string_match_keys:
                if key not in df_to_process.columns:
                    print(f"Warning: String match key '{key}' not found")
                    valid_rows[:] = False
                    break

                for idx, value in df_to_process[key].items():
                    if pd.isna(value):
                        valid_rows[idx] = False

            invalid_count = (~valid_rows).sum()
            if invalid_count > 0:
                print(f"Removed {invalid_count} images with invalid values")

            df_to_process = df_to_process[valid_rows].copy()

            if len(df_to_process) == 0:
                print("No valid images after validation")
                result = df.to_dict('records')
                with open(output_json, 'w', encoding='utf-8') as f:
                    json.dump(_clean_catalog(result), f, indent=2)
                return

            # Create exact match key
            # Convert numeric keys to float for consistent comparison (int vs float)
            match_data = df_to_process[all_match_keys].copy()
            for key in exact_match_keys:
                if key in match_data.columns:
                    match_data[key] = match_data[key].astype(float)
            df_to_process['_exact_key'] = match_data.astype(str).agg('||'.join, axis=1)

        # Find groups with exact matches
        df_to_process['_has_exact_match'] = df_to_process.duplicated(subset='_exact_key', keep=False)

        exact_matches = df_to_process['_has_exact_match'].sum()
        exact_groups = df_to_process[df_to_process['_has_exact_match']]['_exact_key'].nunique()

        print(f"Found {exact_matches} images in {exact_groups} exact match groups")

        # Keep only groups with matches (2+ items)
        df_to_process = df_to_process[df_to_process['_has_exact_match']].copy()

        if len(df_to_process) == 0:
            print("No duplicate groups found")
            result = df.to_dict('records')
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(_clean_catalog(result), f, indent=2)
            return

        # STEP 2: Fuzzy matching
        print(f"\n{'=' * 60}")
        print("STEP 2: Fuzzy matching")
        print(f"{'=' * 60}")

        if fuzzy_match_keys:
            print(f"Fuzzy match keys: {fuzzy_match_keys}, Tolerance: {fuzzy_tolerance * 100}%")

            groups_to_keep = []

            for exact_key_value in df_to_process['_exact_key'].unique():
                group = df_to_process[df_to_process['_exact_key'] == exact_key_value].copy()

                valid_in_group = pd.Series([True] * len(group), index=group.index)

                for key in fuzzy_match_keys:
                    if key not in group.columns:
                        print(f"Warning: Fuzzy key '{key}' not found")
                        continue

                    # Calculate mean for this key in the group
                    values = []
                    for idx, value in group[key].items():
                        if isinstance(value, (int, float)) and value > 0:
                            values.append(value)

                    if len(values) == 0:
                        continue

                    mean_val = np.mean(values)
                    tolerance_range = mean_val * fuzzy_tolerance

                    # Mark rows outside tolerance
                    for idx, value in group[key].items():
                        if not isinstance(value, (int, float)) or value <= 0:
                            valid_in_group[idx] = False
                        elif abs(value - mean_val) > tolerance_range:
                            valid_in_group[idx] = False

                group = group[valid_in_group]

                # Only keep groups with 2+ items
                if len(group) >= 2:
                    groups_to_keep.append(group)

            if groups_to_keep:
                df_to_process = pd.concat(groups_to_keep, ignore_index=False)
                print(f"After fuzzy matching: {len(df_to_process)} images in {len(groups_to_keep)} groups")
            else:
                df_to_process = pd.DataFrame()
                print("No groups passed fuzzy matching")
        else:
            print("Skipping fuzzy match (no keys specified)")

        if len(df_to_process) == 0:
            print("No duplicate groups after fuzzy matching")
            result = df.to_dict('records')
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(_clean_catalog(result), f, indent=2)
            return

        # STEP 3: Date matching
        print(f"\n{'=' * 60}")
        print("STEP 3: Date matching")
        print(f"{'=' * 60}")

        if len(df_to_process) == 0:
            print("No duplicate groups after date matching")
            result = df.to_dict('records')
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(_clean_catalog(result), f, indent=2)
            return

        # STEP 4: Assign matchID
        print(f"\n{'=' * 60}")
        print(f"STEP 4: Assigning {match_id_key}")
        print(f"{'=' * 60}")

        # Create final grouping key
        df_to_process['_final_key'] = df_to_process['_exact_key']

        # Assign unique IDs to each final group
        match_id = 1
        for key_value in df_to_process['_final_key'].unique():
            group_mask = df_to_process['_final_key'] == key_value
            group_size = group_mask.sum()

            if group_size >= 2:
                df_to_process.loc[group_mask, match_id_key] = match_id
                match_id += 1

        # Update main dataframe with match ID
        df.loc[df_to_process.index, match_id_key] = df_to_process[match_id_key]

        # Convert match ID to nullable integer type (Int64) to avoid float conversion
        df[match_id_key] = df[match_id_key].astype('Int64')

        # FINAL STATISTICS
        print(f"\n{'=' * 60}")
        print("RESULTS")
        print(f"{'=' * 60}")

        total_matched = df[match_id_key].notna().sum()
        num_groups = df[match_id_key].nunique()  # nunique() excludes NaN by default

        print(f"Total images with matches: {total_matched}")
        print(f"Number of match groups: {num_groups}")
        print(f"Images without matches: {len(df) - total_matched}")

        # Show sample groups
        if num_groups > 0:
            print(f"\nSample match groups:")
            for match_id_val in df[df[match_id_key].notna()][match_id_key].unique()[:3]:
                group = df[df[match_id_key] == match_id_val]
                print(f"\n  Group {int(match_id_val)} ({len(group)} images):")
                for _, row in group.iterrows():
                    file_info = row.get('SourceFile', row.get('FileName', 'Unknown'))
                    print(f"    - {file_info}")

        # Save updated catalog
        print(f"\nWriting updated catalog to {output_json}...")

        # Convert Int64 (nullable integer) to regular int/None for JSON serialization
        df_copy = df.copy()
        if match_id_key in df_copy.columns:
            df_copy[match_id_key] = df_copy[match_id_key].apply(
                lambda x: int(x) if pd.notna(x) else None
            )

        result = df_copy.to_dict('records')

        # Remove NaN and None values from each record
        cleaned_result = []
        for record in result:
            cleaned_record = {}
            for key, value in record.items():
                # Skip None values
                if value is None:
                    continue
                # Handle scalar values with pd.notna
                try:
                    if isinstance(value, (list, np.ndarray)):
                        # Keep lists and arrays as-is
                        cleaned_record[key] = value
                    elif pd.notna(value):
                        cleaned_record[key] = value
                except (ValueError, TypeError):
                    # If pd.notna fails, keep the value (it's likely a complex type)
                    cleaned_record[key] = value
            cleaned_result.append(cleaned_record)

        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(_clean_catalog(cleaned_result), f, indent=2)

        print("Done!")

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")
        raise