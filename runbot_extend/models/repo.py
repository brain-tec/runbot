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
    forced_branch_ids =  fields.One2many('runbot.forced.branch', 'repo_id', string='Replacing branch names')

    def _git_export(self, treeish, dest):
        res =  super(runbot_repo, self)._git_export(treeish, dest)
        previous_path = '%s/%s' % (dest, 'previous_requirements.txt')
        current_path = '%s/%s' % (dest, 'requirements.txt')
        tmp_path = '%s/%s' % (dest, 'requirements_tmp.txt')

        if os.path.isfile(previous_path) and os.path.isfile(current_path):
            output = open(tmp_path, "wb")
            shutil.copyfileobj(open(previous_path, "rb"), output)
            shutil.copyfileobj(open(current_path, "rb"), output)
            output.close()
            os.remove(previous_path)
            os.remove(current_path)
            shutil.move(tmp_path, current_path)
        if os.path.isfile(current_path):
            shutil.copy(current_path, previous_path)
        return res

class runbot_forced_branch(models.Model):
    _name = "runbot.forced.branch"
    _order = 'sequence, id'

    repo_id = fields.Many2one('runbot.repo', 'Repository', required=True, ondelete='cascade')
    dep_repo_id = fields.Many2one('runbot.repo', required=True, string="For dep. repo",
        help="The target dependency repository for which we want to force branch matching")
    sequence = fields.Integer('Sequence')
    name = fields.Char('Branch name to replace', required=True)
    forced_name = fields.Char('Replacing branch name', required=True)
