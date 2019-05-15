import sys
import os


def get_recursive_filelist(pathlist):
    # Get a recursive list of all files under all path items in the list.
    filelist = []
    if os.path.isdir(pathlist):
        for root, dirs, files in os.walk(pathlist):
            for f in files:
                filelist.append(os.path.join(root, f))
    return filelist


def import_comic_files():
    filelist = get_recursive_filelist("/media2/Comics/Alphabetical/")
    filelist = sorted(filelist, key=os.path.getmtime)
    for file in filelist:
        print(file)


if __name__ == "__main__":
    import_comic_files()
