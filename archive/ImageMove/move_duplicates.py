import pandas as pd
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


def move_duplicates(input_json, duplicates_dir="Duplicates", preferred_paths=None,
                    keep_strategy="keep_shorter_name", dry_run=False, log_file=None,
                    update_catalog=False, output_catalog=None,
                    match_key="DuplicateMatch", use_original_filename=False,
                    removed_catalog=None):
    """
    Process duplicate groups and move files to a Duplicates directory.

    Args:
        input_json: Path to the JSON file with DuplicateMatch IDs
        duplicates_dir: Directory to move duplicate files to
        preferred_paths: Path pattern(s) to prefer keeping. Can be:
                        - A single string (e.g., "iPhotos") for backward compatibility
                        - A list of strings where index determines priority (lower index = higher priority)
                          e.g., ["iPhotos", "Camera", "Downloads"] prefers iPhotos over Camera over Downloads
                        - None defaults to ["iPhotos"]
        keep_strategy: Strategy for choosing which file to keep (used as tiebreaker when
                      multiple files match the same priority level):
                      - "keep_shorter_name": Keep file with shorter filename (default)
                      - "keep_longer_name": Keep file with longer filename
                      - "keep_bigger_file": Keep file with larger FileSize
                      - "keep_smaller_file": Keep file with smaller FileSize
        dry_run: If True, only simulate the move without actually moving files
        log_file: If provided, write log output to this file (in addition to console)
        update_catalog: If True, remove moved files from catalog and save updated version
        output_catalog: Output catalog filename (default: input_json with "_cleaned" suffix)
        match_key: Name of the key(s) containing match IDs (string or list of strings, default: "DuplicateMatch")
                   If a list is provided, items must match on ANY of the keys to be grouped
        use_original_filename: If True, rename files using OriginalFilenameFromPhotos field when moving
        removed_catalog: If provided, save a catalog of removed files (SourceFile only) to this path
    """

    # Helper function to print and optionally log
    def log_print(message, log_handle=None):
        print(message)
        if log_handle:
            log_handle.write(message + '\n')

    # Handle preferred_paths backward compatibility and defaults
    if preferred_paths is None:
        preferred_paths = ["iPhotos"]
    elif isinstance(preferred_paths, str):
        preferred_paths = [preferred_paths]

    log_handle = None
    if log_file:
        try:
            log_handle = open(log_file, 'w', encoding='utf-8')
            log_print(f"Logging to: {log_file}", log_handle)
        except Exception as e:
            print(f"Warning: Could not open log file {log_file}: {e}")
            log_handle = None
    try:
        # Read JSON file
        log_print(f"Reading {input_json}...", log_handle)
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Convert to DataFrame
        df = pd.DataFrame(data)
        log_print(f"Loaded {len(df)} images", log_handle)

        # Create lookup for original filenames if option is enabled
        original_filename_map = {}
        if use_original_filename:
            if 'OriginalFilenameFromPhotos' in df.columns:
                for _, row in df.iterrows():
                    if pd.notna(row.get('OriginalFilenameFromPhotos')) and pd.notna(row.get('SourceFile')):
                        original_filename_map[row['SourceFile']] = row['OriginalFilenameFromPhotos']
                log_print(f"Found {len(original_filename_map)} files with OriginalFilenameFromPhotos", log_handle)
            else:
                log_print("Warning: use_original_filename=True but OriginalFilenameFromPhotos column not found", log_handle)

        # Handle match_key as string or list
        is_multi_key = isinstance(match_key, list)

        if is_multi_key:
            log_print(f"Using multiple match keys: {match_key}", log_handle)

            # Find items with any of the match keys
            has_match = pd.Series([False] * len(df), index=df.index)
            for key in match_key:
                if key in df.columns:
                    has_match = has_match | df[key].notna()

            duplicates_df = df[has_match].copy()

            if len(duplicates_df) == 0:
                log_print(f"No duplicate groups found with any of the keys: {match_key}", log_handle)
                return

            # Create a combined group identifier
            duplicates_df['_combined_group'] = duplicates_df.apply(
                lambda row: next((f"{k}_{row[k]}" for k in match_key if k in row.index and pd.notna(row[k])), None),
                axis=1
            )

            num_groups = duplicates_df['_combined_group'].nunique()
            log_print(f"Found {len(duplicates_df)} images in {num_groups} duplicate group(s)", log_handle)

            group_key = '_combined_group'
        else:
            # Single key mode
            # Filter only items with match key
            duplicates_df = df[df[match_key].notna()].copy()

            if len(duplicates_df) == 0:
                log_print(f"No duplicate groups found in the JSON file (key: {match_key})", log_handle)
                return

            num_groups = duplicates_df[match_key].nunique()
            log_print(f"Found {len(duplicates_df)} images in {num_groups} duplicate group(s)", log_handle)

            group_key = match_key

        if dry_run:
            log_print("\n*** DRY RUN MODE - No files will be moved ***\n", log_handle)

        # Calculate group size statistics
        group_sizes = duplicates_df.groupby(group_key).size()
        groups_with_2 = (group_sizes == 2).sum()
        groups_with_3 = (group_sizes == 3).sum()
        groups_with_more = (group_sizes > 3).sum()

        log_print(f"\nGroup size statistics:", log_handle)
        log_print(f"  Groups with 2 files: {groups_with_2}", log_handle)
        log_print(f"  Groups with 3 files: {groups_with_3}", log_handle)
        log_print(f"  Groups with >3 files: {groups_with_more}", log_handle)

        # Create duplicates directory if it doesn't exist
        if not os.path.exists(duplicates_dir) and not dry_run:
            os.makedirs(duplicates_dir)
            log_print(f"\nCreated directory: {duplicates_dir}", log_handle)
        elif not os.path.exists(duplicates_dir) and dry_run:
            log_print(f"\n[DRY RUN] Would create directory: {duplicates_dir}", log_handle)

        total_moved = 0
        total_not_found = 0  # Track files not found on disk (catalog-only removal)
        moved_files = []  # Track all moved files for catalog update

        # Process each duplicate group
        for dup_id in duplicates_df[group_key].unique():
            group = duplicates_df[duplicates_df[group_key] == dup_id].copy()

            log_print(f"\n{'=' * 60}", log_handle)
            # Format the group ID based on whether it's multi-key or single
            if is_multi_key:
                log_print(f"Processing Group {dup_id} ({len(group)} images)", log_handle)
            else:
                try:
                    log_print(f"Processing Group {int(dup_id)} ({len(group)} images)", log_handle)
                except (ValueError, TypeError):
                    log_print(f"Processing Group {dup_id} ({len(group)} images)", log_handle)
            log_print(f"{'=' * 60}", log_handle)

            # Assign priority based on preferred_paths list (lower index = higher priority)
            def get_priority(source_file):
                for i, pref_path in enumerate(preferred_paths):
                    if pref_path.lower() in source_file.lower():
                        return i
                return len(preferred_paths)  # No match = lowest priority

            group['_priority'] = group['SourceFile'].apply(get_priority)
            has_preferred = group['_priority'] < len(preferred_paths)

            files_to_move = []
            file_to_keep = None

            if has_preferred.any():
                # Keep one preferred file based on priority then strategy, move all others
                best_priority = group['_priority'].min()
                matched_path = preferred_paths[best_priority] if best_priority < len(preferred_paths) else None
                log_print(f"Strategy: Keep one file matching '{matched_path}' (priority {best_priority + 1}/{len(preferred_paths)}, tiebreaker: {keep_strategy}), move all others",
                          log_handle)

                # Get preferred files (those with the best priority) and non-preferred
                best_priority_files = group[group['_priority'] == best_priority].copy()
                other_files = group[group['_priority'] != best_priority]

                # Sort by strategy as tiebreaker for files with same priority
                if keep_strategy == "keep_shorter_name":
                    best_priority_files['_sort_value'] = best_priority_files['SourceFile'].apply(
                        lambda x: len(os.path.basename(x))
                    )
                    best_priority_files = best_priority_files.sort_values('_sort_value', ascending=True)
                elif keep_strategy == "keep_longer_name":
                    best_priority_files['_sort_value'] = best_priority_files['SourceFile'].apply(
                        lambda x: len(os.path.basename(x))
                    )
                    best_priority_files = best_priority_files.sort_values('_sort_value', ascending=False)
                elif keep_strategy == "keep_bigger_file":
                    if 'FileSize' in best_priority_files.columns:
                        best_priority_files['_sort_value'] = best_priority_files['FileSize']
                        best_priority_files = best_priority_files.sort_values('_sort_value', ascending=False)
                    else:
                        log_print(f"  Warning: FileSize not available, falling back to filename length", log_handle)
                        best_priority_files['_sort_value'] = best_priority_files['SourceFile'].apply(
                            lambda x: len(os.path.basename(x))
                        )
                        best_priority_files = best_priority_files.sort_values('_sort_value', ascending=True)
                elif keep_strategy == "keep_smaller_file":
                    if 'FileSize' in best_priority_files.columns:
                        best_priority_files['_sort_value'] = best_priority_files['FileSize']
                        best_priority_files = best_priority_files.sort_values('_sort_value', ascending=True)
                    else:
                        log_print(f"  Warning: FileSize not available, falling back to filename length", log_handle)
                        best_priority_files['_sort_value'] = best_priority_files['SourceFile'].apply(
                            lambda x: len(os.path.basename(x))
                        )
                        best_priority_files = best_priority_files.sort_values('_sort_value', ascending=True)

                # Keep the first (based on strategy), move the rest
                file_to_keep = best_priority_files.iloc[0]['SourceFile']
                if len(best_priority_files) > 1:
                    files_to_move.extend(best_priority_files.iloc[1:]['SourceFile'].tolist())

                # Move all other files (lower priority or no match)
                files_to_move.extend(other_files['SourceFile'].tolist())

            else:
                # No preferred path found, keep file based on strategy
                log_print(f"Strategy: No preferred paths {preferred_paths} found, keeping by {keep_strategy}", log_handle)

                group_copy = group.copy()

                # Sort by strategy
                if keep_strategy == "keep_shorter_name":
                    group_copy['_sort_value'] = group_copy['SourceFile'].apply(
                        lambda x: len(os.path.basename(x))
                    )
                    group_copy = group_copy.sort_values('_sort_value', ascending=True)
                elif keep_strategy == "keep_longer_name":
                    group_copy['_sort_value'] = group_copy['SourceFile'].apply(
                        lambda x: len(os.path.basename(x))
                    )
                    group_copy = group_copy.sort_values('_sort_value', ascending=False)
                elif keep_strategy == "keep_bigger_file":
                    if 'FileSize' in group_copy.columns:
                        group_copy['_sort_value'] = group_copy['FileSize']
                        group_copy = group_copy.sort_values('_sort_value', ascending=False)
                    else:
                        log_print(f"  Warning: FileSize not available, falling back to filename length", log_handle)
                        group_copy['_sort_value'] = group_copy['SourceFile'].apply(
                            lambda x: len(os.path.basename(x))
                        )
                        group_copy = group_copy.sort_values('_sort_value', ascending=True)
                elif keep_strategy == "keep_smaller_file":
                    if 'FileSize' in group_copy.columns:
                        group_copy['_sort_value'] = group_copy['FileSize']
                        group_copy = group_copy.sort_values('_sort_value', ascending=True)
                    else:
                        log_print(f"  Warning: FileSize not available, falling back to filename length", log_handle)
                        group_copy['_sort_value'] = group_copy['SourceFile'].apply(
                            lambda x: len(os.path.basename(x))
                        )
                        group_copy = group_copy.sort_values('_sort_value', ascending=True)

                # Keep the first, move the rest
                file_to_keep = group_copy.iloc[0]['SourceFile']
                files_to_move = group_copy.iloc[1:]['SourceFile'].tolist()

            # Move the files
            for source_file in files_to_move:
                # Determine destination filename
                if use_original_filename and source_file in original_filename_map:
                    filename = original_filename_map[source_file]
                else:
                    filename = os.path.basename(source_file)

                if dry_run:
                    # In dry_run mode, always count files for catalog update
                    if os.path.exists(source_file):
                        destination = os.path.join(duplicates_dir, filename)
                        log_print(f"  [DRY RUN] Would move: {source_file} -> {destination}", log_handle)
                        total_moved += 1
                    else:
                        log_print(f"  [DRY RUN] Would remove from catalog (file not found): {source_file}", log_handle)
                        total_not_found += 1
                    moved_files.append(source_file)
                elif os.path.exists(source_file):
                    destination = os.path.join(duplicates_dir, filename)

                    # Handle filename conflicts
                    if os.path.exists(destination):
                        base, ext = os.path.splitext(filename)
                        counter = 1
                        while os.path.exists(destination):
                            destination = os.path.join(duplicates_dir, f"{base}_{counter}{ext}")
                            counter += 1

                    try:
                        shutil.move(source_file, destination)
                        log_print(f"  Moved: {source_file} -> {destination}", log_handle)
                        total_moved += 1
                        moved_files.append(source_file)
                    except Exception as e:
                        log_print(f"  Error moving {source_file}: {e}", log_handle)
                else:
                    log_print(f"  Skipped (not found): {source_file}", log_handle)

            # Show what was kept
            if file_to_keep:
                if keep_strategy in ["keep_bigger_file", "keep_smaller_file"]:
                    # Show file size if available
                    kept_item = group[group['SourceFile'] == file_to_keep]
                    if len(kept_item) > 0 and 'FileSize' in kept_item.columns:
                        file_size = kept_item.iloc[0]['FileSize']
                        log_print(f"\n  Kept: {file_to_keep} (size: {file_size} bytes)", log_handle)
                    else:
                        log_print(f"\n  Kept: {file_to_keep}", log_handle)
                else:
                    log_print(f"\n  Kept: {file_to_keep} (filename length: {len(os.path.basename(file_to_keep))})",
                              log_handle)

        log_print(f"\n{'=' * 60}", log_handle)
        log_print("SUMMARY", log_handle)
        log_print(f"{'=' * 60}", log_handle)
        log_print(f"Group size distribution:", log_handle)
        log_print(f"  Groups with 2 files: {groups_with_2}", log_handle)
        log_print(f"  Groups with 3 files: {groups_with_3}", log_handle)
        log_print(f"  Groups with >3 files: {groups_with_more}", log_handle)
        log_print("", log_handle)
        if dry_run:
            log_print(f"[DRY RUN] Files that would be moved: {total_moved}", log_handle)
            if total_not_found > 0:
                log_print(f"[DRY RUN] Files not found (catalog-only removal): {total_not_found}", log_handle)
            log_print(f"[DRY RUN] Would move to: {os.path.abspath(duplicates_dir)}", log_handle)
        else:
            log_print(f"Total files moved: {total_moved}", log_handle)
            log_print(f"Moved to: {os.path.abspath(duplicates_dir)}", log_handle)
        log_print("Done!", log_handle)

        # Update catalog if requested
        if update_catalog:
            log_print(f"\n{'=' * 60}", log_handle)
            log_print("UPDATING CATALOG", log_handle)
            log_print(f"{'=' * 60}", log_handle)

            # Determine output filename
            if output_catalog is None:
                base, ext = os.path.splitext(input_json)
                output_catalog = f"{base}_cleaned{ext}"

            # Remove moved files from catalog
            df_cleaned = df[~df['SourceFile'].isin(moved_files)].copy()

            # Remove match key(s) from all items
            if is_multi_key:
                for key in match_key:
                    if key in df_cleaned.columns:
                        df_cleaned = df_cleaned.drop(columns=[key])
                log_print(f"Removed {match_key} keys from all items", log_handle)
            else:
                if match_key in df_cleaned.columns:
                    df_cleaned = df_cleaned.drop(columns=[match_key])
                log_print(f"Removed {match_key} key from all items", log_handle)

            log_print(f"Original catalog items: {len(df)}", log_handle)
            log_print(f"Items removed: {len(moved_files)}", log_handle)
            log_print(f"Cleaned catalog items: {len(df_cleaned)}", log_handle)

            # Save cleaned catalog (always save when update_catalog=True, even in dry_run mode)
            # Remove keys with None or NaN values from each record
            def is_valid_value(v):
                if isinstance(v, (list, dict)):
                    return True  # Keep lists and dicts
                try:
                    return pd.notna(v)
                except ValueError:
                    return True  # Keep if we can't determine

            result = [
                {k: v for k, v in record.items() if is_valid_value(v)}
                for record in df_cleaned.to_dict('records')
            ]
            with open(output_catalog, 'w', encoding='utf-8') as f:
                json.dump(_clean_catalog(result), f, indent=2)
            log_print(f"Saved cleaned catalog to: {output_catalog}", log_handle)

        # Save removed files catalog if requested
        if removed_catalog and moved_files:
            log_print(f"\n{'=' * 60}", log_handle)
            log_print("SAVING REMOVED FILES CATALOG", log_handle)
            log_print(f"{'=' * 60}", log_handle)

            removed_items = [{"SourceFile": f} for f in moved_files]
            with open(removed_catalog, 'w', encoding='utf-8') as f:
                json.dump(removed_items, f, indent=2)
            log_print(f"Saved {len(removed_items)} removed files to: {removed_catalog}", log_handle)

    except FileNotFoundError:
        log_print(f"Error: File not found - {input_json}", log_handle)
    except json.JSONDecodeError:
        log_print(f"Error: Invalid JSON format in {input_json}", log_handle)
    except Exception as e:
        log_print(f"Error: {e}", log_handle)
        raise
    finally:
        if log_handle:
            log_handle.close()
            print(f"\nLog written to: {log_file}")