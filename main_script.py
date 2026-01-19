from PhotoCatalog import *
from ImageCompare import *
from ImageMove import *
from OsxphotosWrap import *
from remove_files import remove_files

PhotoCatalogStart = "/Users/andrea/Downloads/PhotoCatalog.json"

TempCatalog =  "/Users/andrea/Downloads/PhotoCatalog.Temp.json"

DuplicateTaggedCatalog = "/Users/andrea/Downloads/PhotoCatalog.Duplicate.json"

TrashFolder = "/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Trash"

if __name__ == "__main__":

    if (False):
        rename_key(PhotoCatalogStart, TempCatalog, "imageHash", "ImageHash")

    if (False):
        find_images(input_json=PhotoCatalogStart, output_json=TempCatalog,
                filter_keys=None, filter_keys_value=None,
                filter_string_keys=["FileType","Directory"],
                filter_string_keys_value=[["MOV","MP4"],["NoDate"]],
                filter_string_mode=["not_contains","not_contains"],
                exact_match_keys=[], fuzzy_match_keys=None,
                string_match_keys=["FileType", "CreateDate", "ImageHash"],
                match_id_key="PhotosDateHashID")

    if (False):
        open_files_by_key(TempCatalog, "PhotosDateHashID",
                          1332, source_key="SourceFile")

    if (False):
        compare_files_by_key(TempCatalog, "PhotosDateHashID",
                             359, verbose=True, ignore_keys=None)

    if (False):
        compare_mse("/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Photos_Organized/2019/09/28/69BE0DA7-89CC-432F-8AE0-4BDF3ACFB6F6.jpeg",
                    "/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Photos_Organized/2019/09/28/177827C2-DEED-4C98-961E-BC92665E3125.jpeg",
                    threshold=0, verbose=True)

    if (False):
        compare_ssim("/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Photos_Organized/2019/09/28/69BE0DA7-89CC-432F-8AE0-4BDF3ACFB6F6.jpeg",
                    "/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Photos_Organized/2019/09/28/177827C2-DEED-4C98-961E-BC92665E3125.jpeg",
                    threshold=1, verbose=True)

    if (True):
        move_duplicates(input_json=TempCatalog,
                        duplicates_dir="/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Duplicates",
                        preferred_paths=None,
                        keep_strategy="keep_shorter_name",
                        dry_run=False,
                        log_file="/Users/andrea/Downloads/log_file.txt",
                        update_catalog=True,
                        output_catalog=DuplicateTaggedCatalog,
                        match_key="PhotosDateHashID", use_original_filename=True)


    if (False):
        get_unique_values(TempCatalog, key_name="AAE_Files", sort=True, show_counts=True)

    if (False):
        find_images(input_json=TempCatalog, output_json=DuplicateTaggedCatalog,
                filter_keys=None, filter_keys_value=None,
                filter_string_keys=["FileType"],
                filter_string_keys_value=[["AAE"]],
                filter_string_mode=["exact"],
                exact_match_keys=[], fuzzy_match_keys=None,
                string_match_keys=["FileType"],
                match_id_key="AAE_Files")

    if (False):
        remove_files(input_json=DuplicateTaggedCatalog, output_json=PhotoCatalogStart, dry_run=True,
                     key_name="AAE_Files",trash_folder=TrashFolder, update_catalog=True)

