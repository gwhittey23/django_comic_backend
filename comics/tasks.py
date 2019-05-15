from celery import shared_task

from .utils.comicimporter import ComicImporter
from .utils.comicimporter_no_vine import ComicImporterNoVine

@shared_task
def import_comic_files_task():
    ci = ComicImporter()
    success = ci.import_comic_files()

    return success


@shared_task
def refresh_issue_task(cvid):
    print('refresh_task')
    ci = ComicImporter()
    success = ci.refreshIssueData(cvid)

    return success


@shared_task
def refresh_arc_task(cvid):
    ci = ComicImporter()
    success = ci.refreshArcData(cvid)

    return success


@shared_task
def refresh_creator_task(cvid):
    ci = ComicImporter()
    success = ci.refreshCreatorData(cvid)

    return success


@shared_task
def refresh_issue_credits_task(cvid):
    ci = ComicImporter()
    success = ci.refreshIssueCreditsData(cvid)

    return success

#tasks with out using vine but getting info from ComicRack xml in comic archive file

@shared_task
def import_comic_files_novine_task():
    ci = ComicImporterNoVine()
    success = ci.import_comic_files()

    return success

