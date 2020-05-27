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
from odoo.addons.runbot.container import docker_build, docker_run
from odoo.addons.runbot.models.build_config import _re_error, _re_warning
from odoo.addons.runbot.common import dt2time, fqdn, now, grep, time2str, rfind, uniq_list, local_pgadmin_cursor


class runbot_repo(models.Model):
    _inherit = "runbot.repo"

    no_build = fields.Boolean(default=False)
    is_restore = fields.Boolean('Restore database')
    restored_db = fields.Binary(string='Database to restore (zip)', help='Zip file containing an sql dump and a filestore', attachment=True)
    restored_db_filename = fields.Char()
    use_requirements_txt = fields.Boolean('Use requirements.txt', default=True,
        help=("If checked, merge content of repo requirements.txt into main one"
              "(allows installing those dependencies before build)"))
    force_update_all = fields.Boolean('Force Update ALL', help='Force update all on restore otherwise it will update only the modules in the repository', default=False)
    testenable_restore = fields.Boolean('Test enable on upgrade', help='test enabled on update of the restored database', default=False)
    custom_coverage = fields.Char(string='Custom coverage repository',
                                  help='Use --include arg on coverage: list of file name patterns, for example *addons/module1*,*addons/module2*. It only works on sticky branches on nightly coverage builds.')
    custom_config_template = fields.Text('Custom configuration',
                                         help="This config will be placed in a text file, behind the [option] line, and passed with a -c to the jobs.")
    forced_branch_ids =  fields.One2many('runbot.forced.branch', 'repo_id', string='Replacing branch names')
    template_db_name = fields.Char(string='Template database')

class runbot_forced_branch(models.Model):
    _name = "runbot.forced.branch"
    _order = 'sequence, id'

    repo_id = fields.Many2one('runbot.repo', 'Repository', required=True, ondelete='cascade')
    dep_repo_id = fields.Many2one('runbot.repo', required=True, string="For dep. repo",
        help="The target dependency repository for which we want to force branch matching")
    sequence = fields.Integer('Sequence')
    name = fields.Char('Branch name to replace', required=True)
    forced_name = fields.Char('Replacing branch name', required=True)
