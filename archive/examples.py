"""
Template calls for all PhotoCatalog functions.
Copy and modify these templates as needed.
"""

# =============================================================================
# PATH VARIABLES (customize these)
# =============================================================================

MacPhotoLibrary = "/path/to/Photos Library.photoslibrary"
LightroomCatalog = "/path/to/catalog.lrcat"

InputCatalog = "/path/to/input_catalog.json"
OutputCatalog = "/path/to/output_catalog.json"
SecondCatalog = "/path/to/second_catalog.json"

ImageFolders = ["/path/to/folder1", "/path/to/folder2"]
OutputFolder = "/path/to/output_folder"
DuplicatesFolder = "/path/to/duplicates"
TrashFolder = "/path/to/trash"

LogFile = "/path/to/log.txt"


# =============================================================================
# CATALOG CREATION (PhotoCatalog/create_catalog.py)
# =============================================================================

from PhotoCatalog.create_catalog import (
    extract_all_metadata,
    add_image_hash,
    create_catalog_from_mac_photos,
    create_catalog_from_lightroom
)

# Extract metadata from folders using exiftool
extract_all_metadata(
    folder_list=ImageFolders,
    output_json_file=OutputCatalog,
    add_hash=False,              # Set True to add perceptual hash
    hash_key="imageHash"
)

# Add perceptual hash to existing catalog
add_image_hash(
    input_json=InputCatalog,
    output_json=OutputCatalog,
    hash_key="imageHash",
    source_key="SourceFile"
)

# Create catalog from Mac Photos library
create_catalog_from_mac_photos(
    output_json_file=OutputCatalog,
    library_path=MacPhotoLibrary,  # None for default library
    limit=None,                    # None for all, or int to limit
    add_hash=False,
    hash_key="imageHash"
)

# Create catalog from Lightroom catalog
create_catalog_from_lightroom(
    lrcat_path=LightroomCatalog,
    output_json_file=OutputCatalog,
    limit=None,
    add_hash=False,
    hash_key="imageHash",
    use_exiftool=True              # Enrich with exiftool metadata
)


# =============================================================================
# CATALOG MANIPULATION (PhotoCatalog/manipulate_catalog.py)
# =============================================================================

from PhotoCatalog.manipulate_catalog import (
    extract_items,
    apply_filters,
    filter_catalog,
    remove_keys,
    rename_key,
    expand_catalog,
    merge_catalogs,
    clean_catalog,
    add_key_to_catalog,
    add_key
)

# Extract items matching filter criteria
extract_items(
    input_json=InputCatalog,
    output_json=OutputCatalog,      # None to only return data
    filter_keys=["FileType"],
    filter_keys_value=[["JPEG", "PNG"]],
    filter_string_keys=["Directory"],
    filter_string_keys_value=[["/photos", "/images"]],
    filter_string_mode=["contains"]  # "exact", "contains", "excludes"
)

# Filter catalog to keep only specific keys
filter_catalog(
    input_json=InputCatalog,
    output_json=OutputCatalog,
    keep_keys=["SourceFile", "FileType", "CreateDate", "FileSize"]
)

# Remove specific keys from catalog
remove_keys(
    input_json=InputCatalog,
    output_json=OutputCatalog,
    keys_to_remove=["ThumbnailImage", "PreviewImage"]
)

# Rename a key in all items
rename_key(
    input_json=InputCatalog,
    output_json=OutputCatalog,
    original_name="OldKeyName",
    new_name="NewKeyName"
)

# Expand catalog with data from full catalog
expand_catalog(
    input_catalog=InputCatalog,          # Catalog with few keys
    full_catalog=SecondCatalog,          # Full catalog to pull data from
    output_catalog=OutputCatalog,
    main_key="SourceFile"                # Key to match items
)

# Merge multiple catalogs
merge_catalogs(
    catalog_list=[
        "/path/to/catalog1.json",        # Highest priority
        "/path/to/catalog2.json",
        "/path/to/catalog3.json"         # Lowest priority
    ],
    output_catalog=OutputCatalog,
    main_key="SourceFile"                # Key for identifying duplicates
)

# Clean catalog (remove null/NaN values)
clean_catalog(
    input_catalog=InputCatalog,
    output_catalog=OutputCatalog
)

# Add key with value or copy from another key
add_key_to_catalog(
    input_file=InputCatalog,
    output_file=OutputCatalog,
    new_key="NewKeyName",
    value="static_value",                # Or None to use source_key
    source_key=None,                     # Key to copy value from
    replace=False,                       # Replace existing keys?
    indent=2
)

# Add key with optional prefilter
add_key(
    input_file=InputCatalog,
    output_file=OutputCatalog,
    key="NewKeyName",
    value="static_value",
    prefilter=True,                      # Only add to filtered items
    filter_keys=["FileType"],
    filter_keys_value=[["JPEG", "PNG"]],
    filter_string_keys=["Directory"],
    filter_string_keys_value=[["/photos"]],
    filter_string_mode=["contains"],     # "exact", "contains", "excludes"
    replace=False,
    indent=2
)


# =============================================================================
# CATALOG EXPLORATION (PhotoCatalog/explore_catalog.py)
# =============================================================================

from PhotoCatalog.explore_catalog import (
    get_unique_values,
    get_items_missing_key,
    open_files_by_key,
    compare_files_by_key,
    compare_catalogs,
    get_catalog_statistics,
    check_image_files
)

# Get unique values for a key
get_unique_values(
    input_json=InputCatalog,
    key_name="FileType",
    sort=True,
    show_counts=True                     # Show count per value
)

# Get items missing a specific key
get_items_missing_key(
    input_json=InputCatalog,
    key_name="CreateDate",
    output_json=OutputCatalog            # None to only return data
)

# Open files matching a key value
open_files_by_key(
    input_json=InputCatalog,
    key_name="matchID",
    key_value=5,
    source_key="SourceFile",
    app=None                             # None for default, or "Preview"
)

# Compare files with same key value
compare_files_by_key(
    input_json=InputCatalog,
    key_name="matchID",
    key_value=5,
    verbose=True,
    ignore_keys=["SourceFile", "Directory"]
)

# Compare two catalogs
compare_catalogs(
    catalog1=InputCatalog,
    catalog2=SecondCatalog,
    main_key="SourceFile",
    save_unique1="/path/to/unique_in_cat1.json",
    save_unique2="/path/to/unique_in_cat2.json"
)

# Get catalog statistics
get_catalog_statistics(
    input_json=InputCatalog,
    group_by_keys=["FileType", "Directory"],
    compare_json=None,                   # Optional: compare with another catalog
    show_all_keys=True
)

# Check if files in catalog exist on disk
check_image_files(
    json_path=InputCatalog
)


# =============================================================================
# FILE MANIPULATION (PhotoCatalog/manipulate_files.py)
# =============================================================================

from PhotoCatalog.manipulate_files import organize_by_date

# Organize files by date into year/month/day structure
organize_by_date(
    input_catalog=InputCatalog,
    root_folder=OutputFolder,
    date_keys=["CreateDate", "DateTimeOriginal", "ModifyDate"],
    dry_run=True,                        # Set False to actually move
    output_catalog=OutputCatalog,        # Updated catalog with new paths
    log_file=LogFile,
    use_original_filename=False,         # Use OriginalFilenameFromPhotos
    copy_mode=False                      # True=copy, False=move
)


# =============================================================================
# FIND DUPLICATES (ImageCompare/find_images.py)
# =============================================================================

from ImageCompare.find_images import (
    find_images,
    find_duplicate_photos,
    find_duplicate_videos
)

# Find duplicate images based on metadata (full control)
find_images(
    input_json=InputCatalog,
    output_json=OutputCatalog,
    # Filter options (only process matching items)
    filter_keys=["FileType"],
    filter_keys_value=[["JPEG", "PNG"]],
    filter_string_keys=["Directory"],
    filter_string_keys_value=[["/photos"]],
    filter_string_mode=["contains"],     # "exact", "contains", "excludes"
    # Matching options
    exact_match_keys=["ImageWidth", "ImageHeight", "FileSize"],
    string_match_keys=["FileType", "CreateDate"],
    fuzzy_match_keys=["FileSize"],       # Allow slight differences
    fuzzy_tolerance=0.05,                # 5% tolerance
    match_id_key="DuplicateMatchID"      # Key name for match ID
)

# Find duplicate photos (excludes videos, matches on dimensions/hash/date/size)
find_duplicate_photos(
    input_json=InputCatalog,
    output_json=OutputCatalog,
    match_id_key="PhotosDuplicateID"
)

# Find duplicate videos (only MOV/MP4, matches on size/date/dimensions)
find_duplicate_videos(
    input_json=InputCatalog,
    output_json=OutputCatalog,
    match_id_key="MovieDuplicateID"
)


# =============================================================================
# MOVE/REMOVE FILES (ImageMove/)
# =============================================================================

from ImageMove.move_duplicates import move_duplicates
from ImageMove.remove_files import remove_files

# Move duplicate files to a folder
move_duplicates(
    input_json=InputCatalog,
    duplicates_dir=DuplicatesFolder,
    preferred_paths=["iPhotos", "originals"],  # Priority order for keeping
    keep_strategy="keep_shorter_name",   # "keep_shorter_name", "keep_longer_name",
                                         # "keep_bigger_file", "keep_smaller_file"
    dry_run=True,                        # Set False to actually move
    log_file=LogFile,
    update_catalog=True,                 # Remove moved files from catalog
    output_catalog=OutputCatalog,
    match_key="DuplicateMatchID",        # Key(s) containing match IDs
    use_original_filename=False          # Rename using OriginalFilenameFromPhotos
)

# Remove files matching a condition
remove_files(
    input_json=InputCatalog,
    key_name="ToDelete",
    trash_folder=TrashFolder,
    output_json=OutputCatalog,
    key_value=True,                      # None = any non-null value
    source_key="SourceFile",
    dry_run=True,
    update_catalog=True
)


# =============================================================================
# MAC PHOTOS INTEGRATION (OsxphotosWrap/)
# =============================================================================

from OsxphotosWrap.osxphotos_wrapper import export_photos, library_info

# Export photos from Mac Photos library (uses osxphotos CLI)
export_photos(
    library_path=MacPhotoLibrary,
    target_directory=OutputFolder,
    media_type="both",                   # "photos", "movies", "both"
    dry_run=True,
    max_files=None                       # None for all, or int to limit
)

# Get info about Mac Photos library
library_info(
    library_path=MacPhotoLibrary
)


# =============================================================================
# MAC PHOTOS DIRECT ACCESS (PhotoCatalog/mac_photos.py)
# =============================================================================

from PhotoCatalog.mac_photos import (
    PhotosLibrary,
    get_original_filename,
    list_all_photos,
    get_photo_metadata
)

# Initialize PhotosLibrary class
library = PhotosLibrary(library_path=MacPhotoLibrary)

# Get original filename for UUID-based filename
original = get_original_filename(
    uuid_filename="F0E5CF30-3BDD-41AA-8485-575C83193B63.jpg",
    library_path=MacPhotoLibrary
)

# List all photos
photos = list_all_photos(library_path=MacPhotoLibrary)

# Get metadata for a specific photo
metadata = get_photo_metadata(
    uuid_filename="F0E5CF30-3BDD-41AA-8485-575C83193B63.jpg",
    library_path=MacPhotoLibrary
)

# PhotosLibrary class methods
photos = library.list_all_photos(limit=100)
albums = library.get_albums()
album_photos = library.get_photos_in_album("Vacation 2023")
photo_path = library.get_photo_path("F0E5CF30-3BDD-41AA-8485-575C83193B63.jpg")
uuid_map = library.create_uuid_to_original_map()
library.export_mapping_to_json("/path/to/mapping.json")
