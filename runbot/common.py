# -*- coding: utf-8 -*-

import contextlib
import itertools
import logging
import psycopg2
import re
import socket
import time
import os

from collections import OrderedDict
from datetime import timedelta

from babel.dates import format_timedelta
from werkzeug import utils

from odoo.tools.misc import DEFAULT_SERVER_DATETIME_FORMAT

_logger = logging.getLogger(__name__)

# TODO check for accidental drop_db
dest_reg = re.compile(r'^\d{5}-.+-.*$')

class RunbotException(Exception):
    pass

def fqdn():
    return socket.getfqdn()


def time2str(t):
    return time.strftime(DEFAULT_SERVER_DATETIME_FORMAT, t)


def dt2time(datetime):
    """Convert datetime to time"""
    return time.mktime(datetime.timetuple())


def now():
    return time.strftime(DEFAULT_SERVER_DATETIME_FORMAT)


def grep(filename, string):
    if os.path.isfile(filename):
        return find(filename, string) != -1
    return False


def find(filename, string):
    return open(filename).read().find(string)


def uniq_list(l):
    return OrderedDict.fromkeys(l).keys()


def flatten(list_of_lists):
    return list(itertools.chain.from_iterable(list_of_lists))


def rfind(filename, pattern):
    """Determine in something in filename matches the pattern"""
    if os.path.isfile(filename):
        regexp = re.compile(pattern, re.M)
        with open(filename, 'r') as f:
            if regexp.findall(f.read()):
                return True
    return False


def s2human(time):
    """Convert a time in second into an human readable string"""
    return format_timedelta(
        timedelta(seconds=time),
        format="narrow",
        threshold=2.1,
    )


@contextlib.contextmanager
def local_pgadmin_cursor():
    cnx = None
    try:
        cnx = psycopg2.connect("dbname=postgres")
        cnx.autocommit = True  # required for admin commands
        yield cnx.cursor()
    finally:
        if cnx:
            cnx.close()


def list_local_dbs(additionnal_conditions=None):
    additionnal_condition_str = ''
    if additionnal_conditions:
        additionnal_condition_str = 'AND (%s)' % ' OR '.join(additionnal_conditions)
    with local_pgadmin_cursor() as local_cr:
        local_cr.execute("""
            SELECT datname
                FROM pg_database
                WHERE pg_get_userbyid(datdba) = current_user
                %s
        """ % additionnal_condition_str)
        return [d[0] for d in local_cr.fetchall()]


def pseudo_markdown(text):
    text = utils.escape(text)
    patterns = {
            r'\*\*(.+?)\*\*': '<strong>\g<1></strong>',
            r'~~(.+?)~~': '<del>\g<1></del>',  # it's not official markdown but who cares
            r'__(.+?)__': '<ins>\g<1></ins>',  # same here, maybe we should change the method name
            r'`(.+?)`': '<code>\g<1></code>',
    }

    for p, b in patterns.items():
        text = re.sub(p, b, text, flags=re.DOTALL)

    # icons
    re_icon = re.compile(r'@icon-([a-z0-9-]+)')
    text = re_icon.sub('<i class="fa fa-\g<1>"></i>', text)

    # links
    re_links = re.compile(r'\[(.+?)\]\((.+?)\)')
    text = re_links.sub('<a href="\g<2>">\g<1></a>', text)
    return text
