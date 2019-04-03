# -*- encoding: utf-8 -*-

import glob
import io
import logging
import re
import time
import os
import base64
import datetime
import shlex
import subprocess
import shutil

from odoo import models, fields, api, _
from odoo.addons.runbot.container import docker_build, docker_run, build_odoo_cmd
from odoo.addons.runbot.models.build_config import _re_error, _re_warning
from odoo.addons.runbot.common import dt2time, fqdn, now, grep, time2str, rfind, uniq_list, local_pgadmin_cursor, get_py_version


class runbot_repo(models.Model):
    _inherit = "runbot.repo"

    no_build = fields.Boolean(default=False)
    restored_db = fields.Binary(string='Database to restore (zip)', help='Zip file containing an sql dump and a filestore', attachment=True)
    restored_db_filename = fields.Char()
    force_update_all = fields.Boolean('Force Update ALL', help='Force update all on restore otherwise it will update only the modules in the repository', default=False)
    testenable_restore = fields.Boolean('Test enable on upgrade', help='test enabled on update of the restored database', default=False)
    custom_coverage = fields.Char(string='Custom coverage repository',
                                  help='Use --include arg on coverage: list of file name patterns, for example *addons/module1*,*addons/module2*. It only works on sticky branches on nightly coverage builds.')
