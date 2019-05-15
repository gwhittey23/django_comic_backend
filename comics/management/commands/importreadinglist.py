import itertools
import json
import logging
import os
import re
from urllib.parse import unquote_plus
from urllib.request import urlretrieve
from django.core.management.base import BaseCommand, CommandError
from comics.models import Issue
from datetime import datetime, timedelta, date

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from django.utils.text import slugify
from ratelimit import limits, sleep_and_retry
import requests
import requests_cache

from comics.models import (Arc, Creator, Issue, Publisher,
                           Role, Credits, Series, Settings)

from comics.utils import utils
from comics.utils.comicapi.comicarchive import MetaDataStyle, ComicArchive
from comics.utils.comicapi.issuestring import IssueString

from django.utils.text import slugify

import sys, os

def get_recursive_filelist(pathlist):
    # Get a recursive list of all files under all path items in the list.
    filelist = []
    if os.path.isdir(pathlist):
        for root, dirs, files in os.walk(pathlist):
            for f in files:
                filelist.append(os.path.join(root, f))
    return filelist
    

class Command(BaseCommand):
    def __init__(self):
        # Configure logging
        logging.getLogger("requests").setLevel(logging.WARNING)
        self.logger = logging.getLogger('thwip')
        self.read_count = 0
        self.directory_path = Settings.get_solo().comics_directory
    help = 'Imports ComicRack Reading List file turning it into a Reading List in database'
    
    def add_arguments(self, parser):
        
        parser.add_argument(
            '-f', '--file',
            help='Specifies file to which to import.'
            )

    def handle(self, *args, **options):
        crfile = options['file']    
        filelist = get_recursive_filelist(self.directory_path)
        filelist = sorted(filelist, key=os.path.getmtime)

        # Grab the entire issue table into memory
        comics_list = Issue.objects.all()

        # Remove from the database any missing or changed files
        for comic in comics_list:
            print('checkIfRemovedOrModified')
            #self.checkIfRemovedOrModified(comic, self.directory_path)

        comics_list = None

        # Load the issue table again to take into account any
        # issues remove from the database
        c_list = Issue.objects.all()

        # Make a list of all path string in issue table
        db_pathlist = []
        for comic in c_list:
            db_pathlist.append(comic.file)

        c_list = None

        # Now let's remove any existing files in the database
        # from the directory list of files.
        for f in db_pathlist:
            if f in filelist:
                filelist.remove(f)
        db_pathlist = None

        md_list = []
        self.read_count = 0
        for filename in filelist:
            md = self.getComicMetadata(filename)
            if md is not None:
                md_list.append(md)

            if self.read_count % 100 == 0 and self.read_count != 0:
                if len(md_list) > 0:
                    self.commitMetadataList(md_list)
                    md_list = []

        if len(md_list) > 0:
            self.commitMetadataList(md_list)

        self.logger.info('Finished importing..')

    
    def getComicMetadata(self, path):
        # TODO: Need to fix the default image path
        print(path)
        ca = ComicArchive(path, default_image_path=None)
        if ca.seemsToBeAComicArchive():
           
            self.logger.info(f"Reading in {self.read_count} {path}")
            self.read_count += 1
            if ca.hasMetadata(MetaDataStyle.CIX):
                
                style = MetaDataStyle.CIX
            else:
                style = None

            if style is not None:
                
                md = ca.readMetadata(style)
                md.path = ca.path
            
                md.page_count = ca.page_count
                md.mod_ts = datetime.utcfromtimestamp(os.path.getmtime(ca.path))

                return md
        return None

    def addComicFromMetadata(self, md):
        if not md.isEmpty:
            # Let's get the issue Comic Vine id from the archive's metadata
            # If it's not there we'll skip the issue.
            cvID = self.getIssueCVID(md)
            print(cvID)
            if cvID is None:
                issue_name = md.series + ' #' + md.number
                self.logger.info(
                    f'No Comic Vine ID for: {issue_name}... skipping.')
                return False

            # let's get the issue info from CV.
            issue_response = self.getIssue(cvID)
            if issue_response is None:
                return False

            print(md.publisher)

            return True
    
    def getIssueCVID(self, md):
    
        # Get the issues cvid
        # TODO: Need to clean this up a bit, but for now it works.
        cvID = None
        if md.notes is not None:
            cvID = re.search(r'\d+]', md.notes)
            if cvID is not None:
                cvID = str(cvID.group(0))
                cvID = cvID[:-1]
                return cvID

        if md.webLink is not None:
            cvID = re.search(r'/\d+-\d+/', md.webLink)
            if cvID is not None:
                cvID = str(cvID.group(0))
                cvID = cvID.split('-')
                cvID = cvID[1]
                cvID = cvID[:-1]
                return cvID

        return cvID

    def commitMetadataList(self, md_list):
        for md in md_list:
            self.addComicFromMetadata(md)
    
    def getIssue(self, issue_cvid):
        print('issue_cvid')

        return issue_cvid
