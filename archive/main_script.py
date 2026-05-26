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
    check_image_files(
        json_path="/Volumes/CARTIER_BRESSON/Daniela/PhotoCatalog.json"
    )

    # remove_files(
    #     input_json="/Volumes/CARTIER_BRESSON (BCKP)/Daniela/PhotoCatalog.cleaned.json",
    #     key_name="MissingFile",
    #     trash_folder="/Volumes/CARTIER_BRESSON (BCKP)/Daniela/Trash",
    #     output_json="/Volumes/CARTIER_BRESSON (BCKP)/Daniela/PhotoCatalog.final.json",
    #     key_value=True,  # None = any non-null value
    #     source_key="SourceFile",
    #     dry_run=True,
    #     update_catalog=True
    # )