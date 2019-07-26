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
import fnmatch

from odoo import models, fields, api, _
from odoo.addons.runbot.container import docker_build, docker_run, build_odoo_cmd
from odoo.addons.runbot.models.build_config import _re_error, _re_warning
from odoo.addons.runbot.common import dt2time, fqdn, now, grep, time2str, rfind, uniq_list, local_pgadmin_cursor, get_py_version
_logger = logging.getLogger(__name__)


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

    def _get_closest_branch(self, target_repo_id):
        self.ensure_one()
        # 0. force branch name
        name = self.pull_head_name or self.branch_name
        if ':' in name:
            # remove org from pull_head_name (ex: odoo:11.0-opw...)
            name = name.split(':', 1)[1]
        target = self.env['runbot.branch'].browse(target_repo_id)
        for forced_branch in self.repo_id.forced_branch_ids:
            if not (forced_branch.dep_repo_id.id == target_repo_id
                    and fnmatch.fnmatch(name, forced_branch.name)):
                continue
            domain = [
                ('repo_id', '=', target_repo_id),
                ('branch_name', '=', forced_branch.forced_name),
                ('name', '=like', 'refs/heads/%'),
            ]
            targets = self.env['runbot.branch'].search(domain, limit=1)
            if not targets:
                _logger.warning('Could not find forced branch %s on runbot for repository %s', forced_branch.forced_name, target.name)
            if targets and targets[0]._is_on_remote():
                return (targets[0], 'exact')
            _logger.warning('Could not find forced branch %s on remote for repository %s', forced_branch.forced_name, target.name)
        return super(runbot_branch, self)._get_closest_branch(target_repo_id)
