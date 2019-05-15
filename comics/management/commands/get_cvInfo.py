from django.core import management
from datetime import datetime, timedelta, date
import itertools
import json
import logging
import os
import re
from urllib.parse import unquote_plus
from urllib.request import urlretrieve

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from django.utils.text import slugify
from ratelimit import limits, sleep_and_retry
import requests
import requests_cache

from comics.models import (Arc, Creator, Issue, Publisher,
                           Role, Credits, Series, Settings)

from comics.utils.comicapi.comicarchive import ComicArchive
from comics.utils.utils import resize_images
today = date.today()
ISSUES_FOLDER = today.strftime('issues3')
NORMAL_IMG_WIDTH = 640
NORMAL_IMG_HEIGHT = 960
class CVTypeID:
    Issue = '4000'
    Person = '4040'
    Publisher = '4010'
    StoryArc = '4045'
    Volume = '4050'

class Command(management.BaseCommand):
    help = 'Generates a random secret key.'
    def __init__(self):
        self.api_key = Settings.get_solo().api_key
        self.directory_path = Settings.get_solo().comics_directory
        # API Strings
        self.baseurl = 'https://comicvine.gamespot.com/api'
        self.imageurl = 'https://comicvine.gamespot.com/api/image/'
        self.base_params = {'format': 'json',
                            'api_key': self.api_key}
        self.headers = {'user-agent': 'thwip'}
        # API field strings
        self.arc_fields = 'deck,description,id,image,name,site_detail_url'
        self.creator_fields = 'deck,description,id,image,name,site_detail_url'
        self.publisher_fields = 'deck,description,id,image,name,site_detail_url'
        self.series_fields = 'api_detail_url,deck,description,id,name,publisher,site_detail_url,start_year'
        self.issue_fields = 'api_detail_url,cover_date,deck,description,id,image,issue_number'
        self.issue_fields += ',name,site_detail_url,story_arc_credits,volume,person_credits'
    @staticmethod
    def print_me(self):
        pass
        return

    def handle(self, *args, **options):
        ca = ComicArchive('/dataHD/Comics/Spider-Man/v1963/The Amazing Spider-Man V1963 #26 (1965).cbz')
        image_data = ca.getPage(int(0))
        import uuid
        filename = settings.MEDIA_ROOT + '/images/' + str(uuid.uuid4()) + '.jpg'
        with open(filename, 'wb')  as outfile:  
            outfile.write(image_data)
        img = resize_images(filename,
                                    ISSUES_FOLDER,
                                    NORMAL_IMG_WIDTH,
                                    NORMAL_IMG_HEIGHT)
        os.remove(filename)
    