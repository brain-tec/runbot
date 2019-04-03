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


class runbot_build(models.Model):
    _inherit = "runbot.build"

    db_to_restore = fields.Boolean(string='Database to restore')

    def create(self, vals):
        build_id = super(runbot_build, self).create(vals)
        if build_id.repo_id.restored_db:
            build_id.write({'db_to_restore': True})
        return build_id

    @api.model
    def _cron_create_coverage_build(self, hostname):
        if hostname != fqdn():
            return 'Not for me'
        def prefixer(message, prefix):
            m = '[' in message and message[message.index('['):] or message
            if m.startswith(prefix):
                return m
            return '%s%s' % (prefix, m)
        branch_ids = self.env['runbot.branch'].search([
            ('sticky', '=', True),
            ('repo_id.no_build', '=', False),
            ], order='id')
        for branch_id in branch_ids:
            for last_build in self.search([('branch_id', '=', branch_id.id)], limit=1, order='sequence desc'):
                last_build.with_context(force_rebuild=True).create({
                    'branch_id': last_build.branch_id.id,
                    'date': datetime.datetime.now(),
                    'name': last_build.name,
                    'author': last_build.author,
                    'author_email': last_build.author_email,
                    'committer': last_build.committer,
                    'committer_email': last_build.committer_email,
                    'subject': prefixer(last_build.subject, '(coverage)'),
                    'modules': last_build.modules,
                    'extra_params': '',
                    'coverage': True,
                    'job_type': 'testing',
                    'build_type': 'scheduled',
                    'config_id': self.env.ref('runbot.runbot_build_config_test_coverage').id,
                })
