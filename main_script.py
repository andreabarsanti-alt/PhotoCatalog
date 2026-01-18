from PhotoCatalog import *
from ImageCompare import *
from ImageMove import *
from OsxphotosWrap import *

MacPhotoLibrary ="/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Libreria di Foto - Video Backup Extra.photoslibrary"

ExportDir = "/Volumes/CARTIER_BRESSON (BCKP)/Daniela/VideoExport"
ImagesDir = [ExportDir]

PhotoCatalog = '/Users/andrea/Downloads/PhotoCatalog.json'
PhotoCatalogMoreVideos = "/Users/andrea/Downloads/PhotoCatalog.Videos.json"
PhotoCatalogUpdated = "/Users/andrea/Downloads/PhotoCatalog.Updated.json"
DuplicateTaggedCatalog = "/Users/andrea/Downloads/PhotoCatalog.Duplicates.json"
DuplicateTaggedLiteCatalog = "/Users/andrea/Downloads/PhotoCatalog.Duplicates.Lite.json"
DuplicateCleanedCatalog  = "/Users/andrea/Downloads/PhotoCatalog.Duplicates.Cleaned.json"

if __name__ == "__main__":
    # export_photos(
    #     library_path = MacPhotoLibrary,
    #     target_directory = "/Volumes/CARTIER_BRESSON (BCKP)/Daniela/VideoExport",
    #     media_type = "movies",
    #     dry_run = False
    # )
    #
    # extract_all_metadata(ImagesDir, PhotoCatalogMoreVideos, add_hash=True, hash_key="imageHash")
    #
    # merge_catalogs([PhotoCatalog, PhotoCatalogMoreVideos], PhotoCatalogUpdated, main_key="SourceFile")
    #

    # To Find Exact Duplicates, including FileSize and Hash
    # find_images(input_json=PhotoCatalogUpdated, output_json=DuplicateTaggedCatalog,
    #             filter_keys=None, filter_keys_value=None,
    #             filter_string_keys=["FileType"], filter_string_keys_value=[["MOV","MP4"]],filter_string_mode=["exact"],
    #             exact_match_keys=["FileSize"], fuzzy_match_keys=None,
    #             string_match_keys=["FileType", "CreateDate"], match_id_key="MoviesDuplicateID")

    # To check the results
    # filter_catalog(DuplicateTaggedCatalog, DuplicateTaggedLiteCatalog, ["SourceFile", "MoviesDuplicateID"])
    # open_files_by_key(DuplicateTaggedLiteCatalog, "MoviesDuplicateID", 1254, source_key="SourceFile")

    # To Find Exact Duplicates, same Hash (same image) but slightly different filesize
    # find_images(input_json=PhotoCatalog, output_json=DuplicateTaggedCatalog,
    #             filter_keys=None, filter_keys_value=None,
    #             filter_string_keys=["DuplicateMatchID", "FileType"], filter_string_keys_value=[[None],["AAE"]],
    #             filter_string_mode=["exact","not_contains"],
    #             exact_match_keys=["ImageWidth", "ImageHeight"], fuzzy_match_keys=["FileSize"],fuzzy_tolerance=0.0001,
    #             string_match_keys=["FileType", "imageHash"], check_date=True, match_id_key="CopiesMatchID")

    # To check the results
    # open_files_by_key(DuplicateTaggedCatalog, "CopiesMatchID", 2934, source_key="SourceFile")

    # move_duplicates(DuplicateTaggedCatalog, duplicates_dir="/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Duplicates",
    #                dry_run=True, preferred_paths=["iPhotos","originals"], keep_strategy="keep_shorter_name",
    #                log_file="/Users/andrea/Downloads/move_duplicate_log.txt",
    #                update_catalog=True, output_catalog=DuplicateCleanedCatalog,
    #                match_key=["MoviesDuplicateID"])

    check_image_files(DuplicateCleanedCatalog)

