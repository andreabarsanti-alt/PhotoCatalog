import subprocess
import os
import json
import sqlite3
import math
import imagehash
from PIL import Image
import pillow_heif
from typing import Optional

from PhotoCatalog.mac_photos import PhotosLibrary


def _clean_item(item):
    """Remove keys with None or NaN values from an item."""
    return {k: v for k, v in item.items()
            if v is not None and not (isinstance(v, float) and math.isnan(v))}


def _clean_catalog(data):
    """Remove None/NaN values from all items in a catalog."""
    return [_clean_item(item) for item in data]

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
            json.dump(_clean_catalog(data), f, indent=2)

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
        json.dump(_clean_catalog(all_metadata), f, indent=4)

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
        json.dump(_clean_catalog(all_metadata), f, indent=4)

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


def create_catalog_from_lightroom(
        lrcat_path: str,
        output_json_file: str,
        limit: Optional[int] = None,
        add_hash: bool = False,
        hash_key: str = "imageHash",
        use_exiftool: bool = True
) -> None:
    """
    Create a catalog from an Adobe Lightroom catalog file (.lrcat).

    The .lrcat file is an SQLite database containing photo metadata and file paths.
    This function queries the database to extract photo information and optionally
    enriches it with exiftool metadata.

    Args:
        lrcat_path: Path to the Lightroom catalog file (.lrcat)
        output_json_file: Path to save the JSON output
        limit: Optional limit on number of photos to process
        add_hash: If True, compute perceptual hash for each image (default: False)
        hash_key: Name of the key to store the hash (default: "imageHash")
        use_exiftool: If True, enrich catalog with exiftool metadata (default: True)
    """
    if not os.path.exists(lrcat_path):
        raise FileNotFoundError(f"Lightroom catalog not found: {lrcat_path}")

    if not lrcat_path.endswith('.lrcat'):
        print(f"Warning: File does not have .lrcat extension: {lrcat_path}")

    print(f"Opening Lightroom catalog: {lrcat_path}")

    # Connect to the SQLite database
    conn = sqlite3.connect(lrcat_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query to get photo information with full file paths
    # Lightroom stores paths across multiple tables:
    # - AgLibraryRootFolder: root folder paths
    # - AgLibraryFolder: relative folder paths from root
    # - AgLibraryFile: individual file names and metadata
    # - Adobe_images: image-specific metadata (ratings, flags, etc.)
    # - Adobe_imageDevelopSettings: develop/correction settings
    query = """
        SELECT
            img.id_local AS image_id,
            img.rating AS lr_rating,
            img.colorLabels AS lr_color_labels,
            img.pick AS lr_pick,
            img.orientation AS lr_orientation,
            img.captureTime AS lr_capture_time,
            file.id_local AS file_id,
            file.baseName AS base_name,
            file.extension AS extension,
            file.idx_filename AS filename,
            file.importHash AS import_hash,
            file.originalFilename AS original_filename,
            folder.pathFromRoot AS path_from_root,
            root.absolutePath AS root_path,
            root.name AS root_name,
            -- Develop settings (corrections)
            dev.processVersion AS lr_process_version,
            dev.whiteBalance AS lr_white_balance,
            dev.temperature AS lr_temperature,
            dev.tint AS lr_tint,
            dev.exposure AS lr_exposure,
            dev.contrast AS lr_contrast,
            dev.highlights AS lr_highlights,
            dev.shadows AS lr_shadows,
            dev.whites AS lr_whites,
            dev.blacks AS lr_blacks,
            dev.clarity AS lr_clarity,
            dev.vibrance AS lr_vibrance,
            dev.saturation AS lr_saturation,
            dev.sharpness AS lr_sharpness,
            dev.luminanceSmoothing AS lr_luminance_smoothing,
            dev.colorNoiseReduction AS lr_color_noise_reduction,
            dev.vignetteAmount AS lr_vignette_amount,
            dev.cropTop AS lr_crop_top,
            dev.cropLeft AS lr_crop_left,
            dev.cropBottom AS lr_crop_bottom,
            dev.cropRight AS lr_crop_right,
            dev.cropAngle AS lr_crop_angle,
            dev.postCropVignetteAmount AS lr_post_crop_vignette,
            dev.dehaze AS lr_dehaze,
            dev.texture AS lr_texture,
            dev.grayscale AS lr_grayscale,
            dev.hasDevelopAdjustments AS lr_has_adjustments
        FROM Adobe_images img
        JOIN AgLibraryFile file ON img.rootFile = file.id_local
        JOIN AgLibraryFolder folder ON file.folder = folder.id_local
        JOIN AgLibraryRootFolder root ON folder.rootFolder = root.id_local
        LEFT JOIN Adobe_imageDevelopSettings dev ON img.id_local = dev.image
        WHERE file.idx_filename IS NOT NULL
        ORDER BY img.captureTime DESC
    """

    if limit:
        query += f" LIMIT {limit}"

    print("Querying Lightroom database...")
    cursor.execute(query)
    rows = cursor.fetchall()
    print(f"Found {len(rows)} photos in catalog")

    # Build photo list with full paths
    photos = []
    missing_files = 0

    print("Building file paths...")
    for row in rows:
        # Construct full path: root_path + path_from_root + filename
        root_path = row['root_path'] or ''
        path_from_root = row['path_from_root'] or ''
        filename = row['filename'] or ''

        # Root path might end with / and path_from_root might start with /
        full_path = os.path.join(root_path, path_from_root.lstrip('/'), filename)
        full_path = os.path.normpath(full_path)

        # Check if file exists
        file_exists = os.path.exists(full_path)
        if not file_exists:
            missing_files += 1

        photo_info = {
            'SourceFile': full_path,
            'FileName': filename,
            'Directory': os.path.dirname(full_path),
            'BaseName': row['base_name'],
            'Extension': row['extension'],
            'OriginalFilename': row['original_filename'],
            'LR_ImageID': row['image_id'],
            'LR_FileID': row['file_id'],
            'LR_Rating': row['lr_rating'],
            'LR_ColorLabels': row['lr_color_labels'],
            'LR_Pick': row['lr_pick'],
            'LR_Orientation': row['lr_orientation'],
            'LR_CaptureTime': row['lr_capture_time'],
            'LR_ImportHash': row['import_hash'],
            'LR_RootFolder': row['root_name'],
            'FileExists': file_exists,
            # Develop/correction settings
            'LR_ProcessVersion': row['lr_process_version'],
            'LR_WhiteBalance': row['lr_white_balance'],
            'LR_Temperature': row['lr_temperature'],
            'LR_Tint': row['lr_tint'],
            'LR_Exposure': row['lr_exposure'],
            'LR_Contrast': row['lr_contrast'],
            'LR_Highlights': row['lr_highlights'],
            'LR_Shadows': row['lr_shadows'],
            'LR_Whites': row['lr_whites'],
            'LR_Blacks': row['lr_blacks'],
            'LR_Clarity': row['lr_clarity'],
            'LR_Vibrance': row['lr_vibrance'],
            'LR_Saturation': row['lr_saturation'],
            'LR_Sharpness': row['lr_sharpness'],
            'LR_LuminanceSmoothing': row['lr_luminance_smoothing'],
            'LR_ColorNoiseReduction': row['lr_color_noise_reduction'],
            'LR_VignetteAmount': row['lr_vignette_amount'],
            'LR_CropTop': row['lr_crop_top'],
            'LR_CropLeft': row['lr_crop_left'],
            'LR_CropBottom': row['lr_crop_bottom'],
            'LR_CropRight': row['lr_crop_right'],
            'LR_CropAngle': row['lr_crop_angle'],
            'LR_PostCropVignette': row['lr_post_crop_vignette'],
            'LR_Dehaze': row['lr_dehaze'],
            'LR_Texture': row['lr_texture'],
            'LR_Grayscale': row['lr_grayscale'],
            'LR_HasAdjustments': row['lr_has_adjustments'],
        }
        photos.append(photo_info)

    print(f"Processed {len(photos)} photos")
    print(f"Missing files: {missing_files} (files not found on disk)")

    conn.close()

    # Optionally enrich with exiftool metadata
    if use_exiftool:
        print()
        print("Enriching with exiftool metadata...")

        # Group existing photos by directory for efficient exiftool calls
        photos_by_dir = {}
        for photo in photos:
            if not photo['FileExists']:
                continue
            dir_path = photo['Directory']
            if dir_path not in photos_by_dir:
                photos_by_dir[dir_path] = []
            photos_by_dir[dir_path].append(photo)

        print(f"Photos with valid paths spread across {len(photos_by_dir)} directories")

        # Create a mapping from source file to photo for merging
        source_to_photo = {p['SourceFile']: p for p in photos}
        processed_dirs = 0

        for dirpath, dir_photos in photos_by_dir.items():
            processed_dirs += 1
            if processed_dirs % 10 == 0:
                print(f"  Processed {processed_dirs}/{len(photos_by_dir)} directories...")

            # Get list of specific files in this directory
            file_paths = [p['SourceFile'] for p in dir_photos]

            # Call exiftool on specific files
            command = ["exiftool", "-j", "-n"] + file_paths

            try:
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                batch_data = json.loads(result.stdout)

                # Merge exiftool data into our photo records
                for exif_item in batch_data:
                    source_file = exif_item.get('SourceFile')
                    if source_file and source_file in source_to_photo:
                        # Add exiftool fields to our photo record
                        # Preserve our Lightroom-specific fields (prefixed with LR_)
                        photo = source_to_photo[source_file]
                        for key, value in exif_item.items():
                            if key not in photo:
                                photo[key] = value

            except subprocess.CalledProcessError as e:
                print(f"    Error in {dirpath}: {e.stderr}")
            except json.JSONDecodeError:
                print(f"    Error: Could not parse JSON output for {dirpath}")

    # Write the complete dataset to the output file
    print()
    print("-" * 60)
    print(f"Writing data to {output_json_file}...")

    with open(output_json_file, 'w', encoding='utf-8') as f:
        json.dump(_clean_catalog(photos), f, indent=4)

    print(f"Done! Processed a total of {len(photos)} files.")

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