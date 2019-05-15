import os
import re
from shutil import copyfile

from django.core import management
from django.utils.crypto import get_random_string

import thwip
from comics.models import Issue


BASE_DIR = os.path.dirname(bamf.__file__)


class Command(management.BaseCommand):
    help = 'Generates a random secret key.'

    @staticmethod
    def print_me(self):
        pass
        return

    def handle(self, *args, **options):
        c = Issue.objects.all()

        print(c.count())
