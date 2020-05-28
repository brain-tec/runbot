# -*- coding: utf-8 -*-
import datetime
from unittest import skip
from unittest.mock import patch, Mock
from subprocess import CalledProcessError
from odoo.tests import common, TransactionCase
from odoo.tools import mute_logger
import logging
import odoo
import time

from .common import RunbotCase

_logger = logging.getLogger(__name__)


class TestRunbot(RunbotCase):

    def test_warning_from_runbot_abstract(self):
        warning_id = self.env['runbot.runbot'].warning('Test warning message')

        self.assertTrue(self.env['runbot.warning'].browse(warning_id).exists())
