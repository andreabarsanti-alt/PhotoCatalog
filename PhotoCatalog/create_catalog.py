import subprocess
import os
import json
import imagehash
from PIL import Image
import pillow_heif
from typing import Optional

from PhotoCatalog.mac_photos import PhotosLibrary

def add_image_hash(input_json, output_json, hash_key="imageHash", source_key="SourceFile"):
    """
    Add perceptual hash to each image in catalog.
    Supports HEIC/HEIF files in addition to standard formats.

    Args:
        input_json: Path to the input JSON file
        output_json: Path to save the modified JSON file
        hash_key: Name of the key to store the hash (default: "imageHash")
        source_key: Key containing the image file path (default: "SourceFile")
    """

    # Register HEIF opener with PIL
    pillow_heif.register_heif_opener()

    try:
        # Read JSON file
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")
        print(f"Computing perceptual hashes using '{source_key}' field...")
        print("Supported formats: JPEG, PNG, HEIC, HEIF, and other PIL-supported formats")

        # Track statistics
        hashed_count = 0
        error_count = 0
        missing_source = 0

        # Add hash to each item
        for i, item in enumerate(data):
            if i % 100 == 0 and i > 0:
                print(f"  Processed {i}/{len(data)} items...")

            if source_key not in item or item[source_key] is None:
                item[hash_key] = None
                missing_source += 1
                continue

            try:
                img = Image.open(item[source_key])
                img_hash = str(imagehash.phash(img))
                item[hash_key] = img_hash
                hashed_count += 1
            except Exception as e:
                item[hash_key] = None
                error_count += 1
                if error_count <= 5:  # Show first 5 errors
                    print(f"  Error hashing {item[source_key]}: {e}")

        # Print statistics
        print(f"\n{'=' * 60}")
        print("RESULTS")
        print(f"{'=' * 60}")
        print(f"Successfully hashed: {hashed_count} items")
        print(f"Missing source path: {missing_source} items")
        print(f"Errors: {error_count} items")

        # Save modified catalog
        print(f"\nWriting modified catalog to {output_json}...")
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        print("Done!")

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")
        raise

def extract_all_metadata(folder_list, output_json_file, add_hash=False, hash_key="imageHash"):
    """
    Walks through all subdirectories manually and calls ExifTool
    individually for every folder found.

    Args:
        folder_list: List of root folders to scan
        output_json_file: Path to save the JSON output
        add_hash: If True, compute perceptual hash for each image after extraction (default: False)
        hash_key: Name of the key to store the hash (default: "imageHash")
    """
    all_metadata = []

    for root_folder in folder_list:
        if not os.path.exists(root_folder):
            print(f"Skipping: {root_folder} (Path not found)")
            continue

        print(f"Starting walk in: {root_folder}")

        # os.walk(topdown=True) ensures we visit the root then all sub-dirs
        for dirpath, dirnames, filenames in os.walk(root_folder):

            # Only run ExifTool if there are files in this specific directory
            if filenames:
                print(f"  Scanning: {dirpath}")

                # We call ExifTool on the specific directory path
                # No -r needed here because os.walk handles the recursion
                command = ["exiftool", "-j", "-n", dirpath]

                try:
                    result = subprocess.run(command, capture_output=True, text=True, check=True)

                    # Parse and add to our master list
                    batch_data = json.loads(result.stdout)
                    all_metadata.extend(batch_data)

                except subprocess.CalledProcessError as e:
                    print(f"    Error in {dirpath}: {e.stderr}")
                except json.JSONDecodeError:
                    print(f"    Error: Could not parse JSON output for {dirpath}")

    # Write the complete dataset to the output file
    print("-" * 30)
    print(f"Writing data to {output_json_file}...")

    with open(output_json_file, 'w', encoding='utf-8') as f:
        json.dump(all_metadata, f, indent=4)

    print(f"Done! Processed a total of {len(all_metadata)} files.")

    # Add hashes if requested
    if add_hash:
        print("-" * 30)
        print("Adding perceptual hashes...")

        # Create temporary file for hash processing
        temp_file = output_json_file + ".temp"

        # Add hashes using the existing function
        add_image_hash(output_json_file, temp_file, hash_key=hash_key, source_key="SourceFile")

        # Replace original with hashed version
        os.replace(temp_file, output_json_file)
        print(f"Hashes added successfully")


def create_catalog_from_mac_photos(
        output_json_file: str,
        library_path: Optional[str] = None,
        limit: Optional[int] = None,
        add_hash: bool = False,
        hash_key: str = "imageHash"
) -> None:
    """
    Create a catalog from Mac Photos library by querying photos, finding their
    real paths, extracting exiftool metadata, and optionally adding hashes.

    Args:
        output_json_file: Path to save the JSON output
        library_path: Optional custom path to Photos library
        limit: Optional limit on number of photos to process
        add_hash: If True, compute perceptual hash for each image (default: False)
        hash_key: Name of the key to store the hash (default: "imageHash")
    """
    # Initialize Photos library
    print("Connecting to Mac Photos library...")
    library = PhotosLibrary(library_path)
    print(f"Library path: {library.library_path}")

    # Get photos from library
    print(f"Querying photos{f' (limit: {limit})' if limit else ''}...")
    photos = library.list_all_photos(limit=limit)
    print(f"Found {len(photos)} photos")

    # Group photos by directory for efficient exiftool calls
    photos_by_dir = {}
    skipped_no_path = 0

    print("Resolving file paths...")
    for photo in photos:
        uuid_filename = photo['uuid_filename']
        if not uuid_filename:
            skipped_no_path += 1
            continue

        path = library.get_photo_path(uuid_filename)
        if not path or not path.exists():
            skipped_no_path += 1
            continue

        dir_path = str(path.parent)
        if dir_path not in photos_by_dir:
            photos_by_dir[dir_path] = []
        photos_by_dir[dir_path].append({
            'path': str(path),
            'uuid_filename': uuid_filename,
            'original_filename': photo['original_filename']
        })

    print(f"Found {sum(len(v) for v in photos_by_dir.values())} photos with valid paths")
    print(f"Skipped {skipped_no_path} photos (no valid path)")
    print(f"Photos spread across {len(photos_by_dir)} directories")

    # Extract metadata using exiftool for each directory
    all_metadata = []
    processed_dirs = 0

    print()
    print("Extracting metadata with exiftool...")

    for dirpath, dir_photos in photos_by_dir.items():
        processed_dirs += 1
        if processed_dirs % 10 == 0:
            print(f"  Processed {processed_dirs}/{len(photos_by_dir)} directories...")

        # Get list of specific files in this directory
        file_paths = [p['path'] for p in dir_photos]

        # Call exiftool on specific files
        command = ["exiftool", "-j", "-n"] + file_paths

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            batch_data = json.loads(result.stdout)

            # Add original filename from Photos library to each item
            path_to_original = {p['path']: p['original_filename'] for p in dir_photos}
            for item in batch_data:
                source_file = item.get('SourceFile')
                if source_file and source_file in path_to_original:
                    item['OriginalFilenameFromPhotos'] = path_to_original[source_file]

            all_metadata.extend(batch_data)

        except subprocess.CalledProcessError as e:
            print(f"    Error in {dirpath}: {e.stderr}")
        except json.JSONDecodeError:
            print(f"    Error: Could not parse JSON output for {dirpath}")

    # Write the complete dataset to the output file
    print()
    print("-" * 60)
    print(f"Writing data to {output_json_file}...")

    with open(output_json_file, 'w', encoding='utf-8') as f:
        json.dump(all_metadata, f, indent=4)

    print(f"Done! Processed a total of {len(all_metadata)} files.")

    # Add hashes if requested
    if add_hash:
        print("-" * 60)
        print("Adding perceptual hashes...")

        # Create temporary file for hash processing
        temp_file = output_json_file + ".temp"

        # Add hashes using the existing function
        add_image_hash(output_json_file, temp_file, hash_key=hash_key, source_key="SourceFile")

        # Replace original with hashed version
        os.replace(temp_file, output_json_file)
        print(f"Hashes added successfully")
