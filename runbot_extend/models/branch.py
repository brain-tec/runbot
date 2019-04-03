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

class runbot_branch(models.Model):
    _inherit = "runbot.branch"

    def create(self, vals):
        branch_id = super(runbot_branch, self).create(vals)
        if branch_id.repo_id.no_build:
            branch_id.write({'no_build': True})
        return branch_id


    def _get_branch_quickconnect_url(self, fqdn, dest):
        self.ensure_one()
        if self.repo_id.restored_db:
            r = {}
            r[self.id] = "http://%s/web/login?db=%s-restored&login=admin&redirect=/web?debug=1" % (
                fqdn, dest)
        else:
            r = super(runbot_branch, self)._get_branch_quickconnect_url(
                fqdn, dest)
        return r
