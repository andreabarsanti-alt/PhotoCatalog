from PhotoCatalog import *
from ImageCompare import *
from ImageMove import *
from OsxphotosWrap import *
from remove_files import remove_files

PhotoCatalogA = "/Users/andrea/Downloads/PhotoCatalog.json"
PhotoCatalogB = "/Users/andrea/Downloads/MacPhotos.json"
PhotoCatalogMerged = "/Users/andrea/Downloads/PhotoCatalog.Merged.json"
TempCatalog =  "/Users/andrea/Downloads/PhotoCatalog.Temp.json"
DuplicateTaggedCatalog = "/Users/andrea/Downloads/PhotoCatalog.Duplicate.json"
FinalCatalog = "/Users/andrea/Downloads/PhotoCatalog.Final.json"
PhotosRemoved = "/Users/andrea/Downloads/PhotosRemoved.json"
MoviesRemoved = "/Users/andrea/Downloads/MoviesRemoved.json"

TrashFolder = "/Volumes/CARTIER_BRESSON/Daniela/Trash"
DuplicateFolder = "/Volumes/CARTIER_BRESSON/Daniela/Duplicates"

if __name__ == "__main__":
    move_duplicates(
        input_json=DuplicateTaggedCatalog,
        duplicates_dir=DuplicateFolder,
        preferred_paths=["Photos_Organized"],  # Priority order for keeping
        keep_strategy="keep_shorter_name",   # "keep_shorter_name", "keep_longer_name",
                                             # "keep_bigger_file", "keep_smaller_file"
        dry_run=True,                        # Set False to actually move
        log_file="/Users/andrea/Downloads/Log.photos.txt",
        update_catalog=True,                 # Remove moved files from catalog
        output_catalog=TempCatalog,
        match_key="PhotosDuplicateID",        # Key(s) containing match IDs
        removed_catalog=PhotosRemoved,
        use_original_filename=True          # Rename using OriginalFilenameFromPhotos
    )

    move_duplicates(
        input_json=TempCatalog,
        duplicates_dir=DuplicateFolder,
        preferred_paths=["Photos_Organized"],  # Priority order for keeping
        keep_strategy="keep_shorter_name",  # "keep_shorter_name", "keep_longer_name",
        # "keep_bigger_file", "keep_smaller_file"
        dry_run=True,  # Set False to actually move
        log_file="/Users/andrea/Downloads/Log.movies.txt",
        update_catalog=True,  # Remove moved files from catalog
        output_catalog=FinalCatalog,
        match_key="MovieDuplicateID",  # Key(s) containing match IDs
        removed_catalog=MoviesRemoved,
        use_original_filename=True  # Rename using OriginalFilenameFromPhotos
    )

