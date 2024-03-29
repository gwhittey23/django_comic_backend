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

from . import utils
from .comicapi.comicarchive import MetaDataStyle, ComicArchive
from .comicapi.issuestring import IssueString


today = date.today()
ARCS_FOLDER = today.strftime('arcs/%Y/%m/%d')
CREATORS_FOLDERS = today.strftime('creators/%Y/%m/%d')
ISSUES_FOLDER = today.strftime('issues/%Y/%m/%d')
PUBLISHERS_FOLDER = today.strftime('publishers/%Y/%m/%d')

CREATOR_IMG_WIDTH = 64
CREATOR_IMG_HEIGHT = 64

NORMAL_IMG_WIDTH = 640
NORMAL_IMG_HEIGHT = 960

ONE_MINUTE = 60


def get_recursive_filelist(pathlist):
    # Get a recursive list of all files under all path items in the list.
    filelist = []
    if os.path.isdir(pathlist):
        for root, dirs, files in os.walk(pathlist):
            for f in files:
                filelist.append(os.path.join(root, f))
    return filelist


class CVTypeID:
    Issue = '4000'
    Person = '4040'
    Publisher = '4010'
    StoryArc = '4045'
    Volume = '4050'


class ComicImporter(object):

    def __init__(self):
        # Configure logging
        logging.getLogger("requests").setLevel(logging.WARNING)
        self.logger = logging.getLogger('thwip')
        # Setup requests caching
        expire_after = timedelta(hours=1)
        requests_cache.install_cache('cv-cache',
                                     backend='redis',
                                     expire_after=expire_after)
        requests_cache.core.remove_expired_responses()
        # temporary values until settings view is created.
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
        # Initial Comic Book info to search
        self.style = MetaDataStyle.CIX

    def checkIfRemovedOrModified(self, comic, pathlist):
        remove = False

        def inFolderlist(filepath, pathlist):
            for p in pathlist:
                if p in filepath:
                    return True
            return False

        if not (os.path.exists(comic.file)):
            self.logger.info(f"Removing missing {comic.file}")
            remove = True
        elif not inFolderlist(comic.file, pathlist):
            self.logger.info(f"Removing unwanted {comic.file}")
            remove = True
        else:
            current_timezone = timezone.get_current_timezone()
            c = datetime.utcfromtimestamp(os.path.getmtime(comic.file))
            curr = timezone.make_aware(c, current_timezone)
            prev = comic.mod_ts

            if curr != prev:
                self.logger.info(f"Removing modified {comic.file}")
                remove = True

        if remove:
            series = Series.objects.get(id=comic.series.id)
            s_count = series.issue_count
            # If this is the only issue for a series, delete the series.
            if s_count == 1:
                series.delete()
                self.logger.info(f'Deleting series: {series}')
            else:
                comic.delete()

    def getCVObjectData(self, response):
        '''
        Gathers object data from a response and tests each value to make sure
        it exists in the response before trying to set it.

        CVID and CVURL will always exist in a ComicVine response, so there
        is no need to verify this data.

        Returns a dictionary with all the gathered data.
        '''

        # Get Name
        name = ''
        if 'name' in response:
            if response['name']:
                name = response['name']

        # Get Start Year (only exists for Series objects)
        year = ''
        if 'start_year' in response:
            if response['start_year']:
                year = response['start_year']

        # Get Number (only exists for Issue objects)
        number = ''
        if 'issue_number' in response:
            if response['issue_number']:
                number = response['issue_number']

        # Get Description (Favor short description if available)
        desc = ''
        if 'deck' in response:
            if response['deck']:
                # Check to see if the deck is a space (' ').
                if response['deck'] != ' ':
                    desc = response['deck']
            if desc == '':
                if 'description' in response:
                    if response['description']:
                        desc = response['description']

        # Get Image
        image = ''
        if 'image' in response:
            if response['image']:
                image_url = self.imageurl + \
                    response['image']['super_url'].rsplit('/', 1)[-1]
                image_filename = unquote_plus(image_url.split('/')[-1])
                if image_filename != '1-male-good-large.jpg' and not re.match(".*question_mark_large.*.jpg", image_filename):
                    try:
                        image = utils.test_image(urlretrieve(
                            image_url, 'media/images/' + image_filename)[0])
                    except OSError as e:
                        self.logger.error(
                            f'getCVObjectData retrieve image - {e}')
                        image = None

        # Create data object
        data = {
            'cvid': response['id'],
            'cvurl': response['site_detail_url'],
            'name': name,
            'year': year,
            'number': number,
            'desc': utils.cleanup_html(desc, True),
            'image': image,
        }

        return data

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def refreshCreatorData(self, cvid):
        issue_params = self.base_params
        issue_params['field_list'] = self.creator_fields

        try:
            resp = requests.get(
                self.baseurl + '/person/' + CVTypeID.Person + '-' + str(cvid),
                params=issue_params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'refreshCreatorData - {e}')
            return False

        if not (resp['results']):
            return False

        data = self.getCVObjectData(resp['results'])

        creator_obj = Creator.objects.get(cvid=cvid)

        if data['image'] != '':
            if (creator_obj.image):
                creator_obj.image.delete()
            creator_obj.image = utils.resize_images(data['image'],
                                                    CREATORS_FOLDERS,
                                                    CREATOR_IMG_WIDTH,
                                                    CREATOR_IMG_HEIGHT)
            os.remove(data['image'])

        creator_obj.name = data['name']
        creator_obj.desc = data['desc']
        creator_obj.save()
        self.logger.info(f'Refresh metadata for: {creator_obj}')

        return True

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def refreshIssueData(self, cvid):
        issue_params = self.base_params
        issue_params['field_list'] = self.issue_fields

        try:
            resp = requests.get(
                self.baseurl + '/issue/' + CVTypeID.Issue + '-' + str(cvid),
                params=issue_params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'refreshIssueData - {e}')
            return False

        if not (resp['results']):
            return False

        data = self.getCVObjectData(resp['results'])

        issue_obj = Issue.objects.get(cvid=cvid)

        # Clear any arcs the issue might have.
        issue_obj.arcs.clear()

        # Add any story arcs.
        self.addIssueStoryArcs(cvid, resp['results']['story_arc_credits'])

        # TODO: Makes sense to move the image refresh into a
        #       separate function but for now let's leave it here.
        if data['image'] != '
            # Delete the existing image before adding the new one.
            if (issue_obj.image):
                issue_obj.image.delete()
            # Resize the image and save the new image then
            # remove the original.
            issue_obj.image = utils.resize_images(data['image'],
                                                  ISSUES_FOLDER,
                                                  NORMAL_IMG_WIDTH,
                                                  NORMAL_IMG_HEIGHT)
            os.remove(data['image'])

        issue_obj.desc = data['desc']
        issue_obj.name = data['name']
        issue_obj.save()

        self.logger.info(f'Refreshed metadata for: {issue_obj}')

        return True

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def refreshIssueCreditsData(self, cvid):
        issue_params = self.base_params
        issue_params['field_list'] = 'person_credits'

        try:
            resp = requests.get(
                self.baseurl + '/issue/' + CVTypeID.Issue + '-' + str(cvid),
                params=issue_params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'refreshIssueCredits - {e}')
            return False

        if not (resp['results']):
            return False

        issue_obj = Issue.objects.get(cvid=cvid)

        # Delete any existing issue credits.
        Credits.objects.filter(issue=issue_obj).delete()

        # Add new issue credits
        self.addIssueCredits(cvid, resp['results']['person_credits'])

        self.logger.info(f'Refreshed credits for: {issue_obj}')

        return True

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def refreshSeriesData(self, cvid):
        issue_params = self.base_params
        issue_params['field_list'] = self.series_fields

        try:
            resp = requests.get(
                self.baseurl + '/volume/' + CVTypeID.Volume + '-' + str(cvid),
                params=issue_params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'refreshSeriesData - {e}')
            return False

        if not (resp['results']):
            return False

        data = self.getCVObjectData(resp['results'])

        series = Series.objects.get(cvid=cvid)
        series.desc = data['desc']
        series.year = data['year']
        series.save()
        self.logger.info(f'Refreshed metadata for: {series}')

        return True

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def refreshPublisherData(self, cvid):
        issue_params = self.base_params
        issue_params['field_list'] = self.publisher_fields

        try:
            resp = requests.get(
                self.baseurl + '/publisher/' +
                CVTypeID.Publisher + '-' + str(cvid),
                params=issue_params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'refreshPublisherData - {e}')
            return False

        if not (resp['results']):
            return False

        data = self.getCVObjectData(resp['results'])

        # Currently I'm not refreshing the image until the
        # cropping code is refactored, so let's remove the image.
        os.remove(data['image'])

        publisher = Publisher.objects.get(cvid=cvid)
        publisher.desc = data['desc']
        publisher.save()
        self.logger.info(f'Refresh metadata for: {publisher}')

        return True

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def refreshArcData(self, cvid):
        issue_params = self.base_params
        issue_params['field_list'] = self.arc_fields

        try:
            resp = requests.get(
                self.baseurl + '/story_arc/' +
                CVTypeID.StoryArc + '-' + str(cvid),
                params=issue_params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error('%s' % e)
            return False

        if not (resp['results']):
            return False

        data = self.getCVObjectData(resp['results'])

        arc_obj = Arc.objects.get(cvid=cvid)

        if data['image'] != '':
            if (arc_obj.image):
                arc_obj.image.delete()

            arc_obj.image = utils.resize_images(data['image'],
                                                ARCS_FOLDER,
                                                NORMAL_IMG_WIDTH,
                                                NORMAL_IMG_HEIGHT)
            os.remove(data['image'])

        arc_obj.desc = data['desc']
        arc_obj.save()
        self.logger.info(f'Refreshed metadata for: {arc_obj}')

        return True

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def getIssue(self, issue_cvid):
        issue_params = self.base_params
        issue_params['field_list'] = self.issue_fields

        try:
            response = requests.get(
                self.baseurl + '/issue/' +
                CVTypeID.Issue + '-' + str(issue_cvid),
                params=issue_params,
                headers=self.headers,
            ).json()
        except (requests.exceptions.RequestException, json.decoder.JSONDecodeError) as e:
            self.logger.error(f'getIssue - {e}')
            response = None

        return response

    def setIssueDetail(self, issue_cvid, issue_response):

        data = self.getCVObjectData(issue_response['results'])

        issue = Issue.objects.get(cvid=issue_cvid)
        if data['image'] != '' or None:
            img = utils.resize_images(data['image'],
                                      ISSUES_FOLDER,
                                      NORMAL_IMG_WIDTH,
                                      NORMAL_IMG_HEIGHT)
            if img:
                issue.image = img
            os.remove(data['image'])
        issue.desc = data['desc']
        issue.save()

        return True

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def getSeriesDetail(self, api_url):
        params = self.base_params
        params['field_list'] = self.series_fields

        try:
            response = requests.get(
                api_url,
                params=params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'getSeriesDetail - {e}')
            return None

        data = self.getCVObjectData(response['results'])

        return data

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def getPublisherData(self, response_issue):
        series_params = self.base_params
        series_params['field_list'] = 'publisher'

        try:
            response_series = requests.get(
                response_issue['results']['volume']['api_detail_url'],
                params=series_params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'getPublisherData(volume) - {e}')
            return None

        params = self.base_params
        params['field_list'] = self.publisher_fields

        api_url = response_series['results']['publisher']['api_detail_url']

        try:
            response = requests.get(
                api_url,
                params=params,
                headers=self.headers,
            ).json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f'getPublisherData(publisher) - {e}')
            return None

        data = self.getCVObjectData(response['results'])

        return data

    @sleep_and_retry
    @limits(calls=7, period=ONE_MINUTE)
    def getDetailInfo(self, db_obj, fields, api_url):
        params = self.base_params
        params['field_list'] = fields

        try:
            response = requests.get(
                api_url,
                params=params,
                headers=self.headers,
            ).json()
        except (requests.exceptions.RequestException, json.decoder.JSONDecodeError) as e:
            self.logger.error(f'getDetailInfo - {e}')
            return False

        data = self.getCVObjectData(response['results'])

        # Year (only exists for Series objects)
        if data['year'] is not None:
            db_obj.year = data['year']
        db_obj.cvurl = data['cvurl']
        db_obj.desc = data['desc']
        # If the image name from Comic Vine is too large, don't save it since it will
        # cause a DB error. Using 132 as the value since that will take into account the
        # upload_to value from the longest model (Pubishers).
        try:
            if (len(data['image']) < 132):
                db_obj.image = data['image']
            db_obj.save()

            return True
        except TypeError:
            return False
    def addIssueStoryArcs(self, issue_cvid, arc_response):
        issue_obj = Issue.objects.get(cvid=issue_cvid)
        for arc in arc_response:
            arc_obj = self.getStoryArc(arc)
            if arc_obj:
                issue_obj.arcs.add(arc_obj)

    def getStoryArc(self, arcResponse):
        story_obj, s_create = Arc.objects.get_or_create(
            cvid=arcResponse['id'],)

        if s_create:
            new_slug = orig = slugify(arcResponse['name'])
            for x in itertools.count(1):
                if not Arc.objects.filter(slug=new_slug).exists():
                    break
                new_slug = f'{orig}-{x}'

            story_obj.name = arcResponse['name']
            story_obj.slug = new_slug
            story_obj.save()

            res = self.getDetailInfo(story_obj,
                                     self.arc_fields,
                                     arcResponse['api_detail_url'])

            if story_obj.image:
                self.create_arc_images(story_obj, ARCS_FOLDER)

            if res:
                self.logger.info(f'Added storyarc: {story_obj}')
            else:
                self.logger.info(
                    f'Not Story Arc detail info available for: {story_obj}')

        return story_obj

    def addIssueCredits(self, issue_cvid, credits_response):
        issue_obj = Issue.objects.get(cvid=issue_cvid)
        for p in credits_response:
            creator_obj = self.getCreator(p)
            credits_obj = Credits.objects.create(
                creator=creator_obj, issue=issue_obj)

            roles = p['role'].split(',')
            for role in roles:
                # Remove any whitespace
                role = role.strip()
                r, r_create = Role.objects.get_or_create(name=role.title())
                credits_obj.role.add(r)

    def getCreator(self, creatorResponse):
        creator_obj, c_create = Creator.objects.get_or_create(
            cvid=creatorResponse['id'],)

        if c_create:
            new_slug = orig = slugify(creatorResponse['name'])
            for x in itertools.count(1):
                if not Creator.objects.filter(slug=new_slug).exists():
                    break
                new_slug = f'{orig}-{x}'

            creator_obj.name = creatorResponse['name']
            creator_obj.slug = new_slug
            creator_obj.save()

            res = self.getDetailInfo(creator_obj,
                                     self.creator_fields,
                                     creatorResponse['api_detail_url'])

            if creator_obj.image:
                self.create_images(creator_obj, CREATORS_FOLDERS)

            if res:
                self.logger.info(f'Added creator: {creator_obj}')
            else:
                self.logger.info(
                    f'No Creator detail info available for: {creator_obj}')

        return creator_obj

    def getSeries(self, issueResponse):
        series_cvid = issueResponse['results']['volume']['id']

        series_obj, s_create = Series.objects.get_or_create(
            cvid=int(series_cvid),)

        if s_create:
            series_url = issueResponse['results']['volume']['api_detail_url']
            data = self.getSeriesDetail(series_url)
            if data is not None:
                # Create the slug & make sure it's not a duplicate
                new_slug = orig = slugify(data['name'])
                for x in itertools.count(1):
                    if not Series.objects.filter(slug=new_slug).exists():
                        break
                    new_slug = f'{orig}-{x}'

            sort_name = utils.create_series_sortname(data['name'])
            series_obj.slug = new_slug
            series_obj.cvurl = data['cvurl']
            series_obj.name = data['name']
            series_obj.sort_title = sort_name
            series_obj.year = data['year']
            series_obj.desc = data['desc']
            series_obj.save()
            self.logger.info(f'Added series: {series_obj}')

        return series_obj

    def getPublisher(self, publisher, issueResponse):
        publisher_obj, p_create = Publisher.objects.get_or_create(name=publisher,
                                                                  slug=slugify(publisher),)

        if p_create:
            p = self.getPublisherData(issueResponse)
            if p is not None:
                publisher_obj.cvid = int(p['cvid'])
                publisher_obj.cvurl = p['cvurl']
                publisher_obj.desc = p['desc']
                if p['image'] is not '':
                    publisher_obj.image = utils.resize_images(p['image'],
                                                              PUBLISHERS_FOLDER,
                                                              NORMAL_IMG_WIDTH,
                                                              NORMAL_IMG_HEIGHT)
                    # Delete the original image
                    os.remove(p['image'])
                publisher_obj.save()
            self.logger.info(f'Added publisher: {publisher_obj}')

        return publisher_obj

    def create_arc_images(self, db_obj, img_dir):
        base_name = os.path.basename(db_obj.image.name)
        old_image_path = settings.MEDIA_ROOT + '/images/' + base_name
        db_obj.image = utils.resize_images(db_obj.image, img_dir,
                                           NORMAL_IMG_WIDTH, NORMAL_IMG_HEIGHT)
        db_obj.save()
        os.remove(old_image_path)

    # Only the Creators are using this right now.
    def create_images(self, db_obj, img_dir):
        base_name = os.path.basename(db_obj.image.name)
        old_image_path = settings.MEDIA_ROOT + '/images/' + base_name
        db_obj.image = utils.resize_images(db_obj.image, img_dir,
                                           CREATOR_IMG_WIDTH, CREATOR_IMG_HEIGHT)
        db_obj.save()
        os.remove(old_image_path)

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

    def createPubDate(self, day, month, year):
        pub_date = None
        if year is not None:
            try:
                new_day = 1
                new_month = 1
                if month is not None:
                    new_month = int(month)
                if day is not None:
                    new_day = int(day)
                new_year = int(year)
                pub_date = datetime(new_year, new_month, new_day)
            except:
                pass

        return pub_date

    def createIssueSlug(self, pubDate, fixedNumber, seriesName):
        if pubDate is not None:
            slugy = seriesName + ' ' + fixedNumber + ' ' + str(pubDate.year)
        else:
            slugy = seriesName + ' ' + fixedNumber

        new_slug = orig = slugify(slugy)

        for x in itertools.count(1):
            if not Issue.objects.filter(slug=new_slug).exists():
                break
            new_slug = f'{orig}-{x}'

        return new_slug

    def getComicMetadata(self, path):
        # TODO: Need to fix the default image path
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
            if cvID is None:
                issue_name = md.series + ' #' + md.number
                self.logger.info(
                    f'No Comic Vine ID for: {issue_name}... skipping.')
                return False

            # let's get the issue info from CV.
            issue_response = self.getIssue(cvID)
            if issue_response is None:
                return False

            # Get or create the Publisher.
            if md.publisher is not None:
                publisher_obj = self.getPublisher(md.publisher, issue_response)

            # Get or create the series and if a publisher is available set it.
            series_obj = self.getSeries(issue_response)
            if publisher_obj:
                series_obj.publisher = publisher_obj
                series_obj.save()

            # Ugh, deal wih the timezone
            current_timezone = timezone.get_current_timezone()
            tz = timezone.make_aware(md.mod_ts, current_timezone)

            pub_date = self.createPubDate(md.day, md.month, md.year)
            fixed_number = IssueString(md.issue).asString(pad=3)
            issue_slug = self.createIssueSlug(
                pub_date, fixed_number, series_obj.name)

            try:
                # Create the issue
                issue_obj = Issue.objects.create(
                    file=md.path,
                    name=str(md.title),
                    slug=issue_slug,
                    number=fixed_number,
                    date=pub_date,
                    page_count=md.page_count,
                    cvurl=md.webLink,
                    cvid=int(cvID),
                    mod_ts=tz,
                    series=series_obj,)
            except IntegrityError as e:
                self.logger.error(f'Attempting to create issue in db - {e}')
                self.logger.info(f'Skipping: {md.path}')
                return

            # Set the issue image & short description.
            res = self.setIssueDetail(cvID, issue_response)
            if res:
                self.logger.info(f"Added: {issue_obj}")
            else:
                self.logger.warning(
                    f'No detail information was saved for {issue_obj}')

            # Add the storyarc.
            self.addIssueStoryArcs(issue_obj.cvid,
                                   issue_response['results']['story_arc_credits'])

            # Add the creators
            self.addIssueCredits(issue_obj.cvid,
                                 issue_response['results']['person_credits'])
 
            return True

    def commitMetadataList(self, md_list):
        for md in md_list:
            self.addComicFromMetadata(md)

    def import_comic_files(self):
        filelist = get_recursive_filelist(self.directory_path)
        filelist = sorted(filelist, key=os.path.getmtime)

        # Grab the entire issue table into memory
        comics_list = Issue.objects.all()

        # Remove from the database any missing or changed files
        for comic in comics_list:
            self.checkIfRemovedOrModified(comic, self.directory_path)

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
