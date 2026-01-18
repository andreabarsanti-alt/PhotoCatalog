import subprocess
import os
import json
import imagehash
from PIL import Image
import pillow_heif

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
