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


class runbot_build(models.Model):
    _inherit = "runbot.build"

    db_to_restore = fields.Boolean(string='Database to restore')

    def create(self, vals):
        build_id = super(runbot_build, self).create(vals)
        if build_id.repo_id.is_restore and build_id.local_state != 'duplicate':
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
            ('coverage', '=', True),
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
                    'extra_params': '',
                    'coverage': True,
                    'build_type': 'scheduled',
                    'config_id': self.env.ref('runbot.runbot_build_config_test_coverage').id,
                })

    def _checkout(self, commits=None):
        exports = super(runbot_build, self)._checkout(commits=commits)

        # requirements.txt: generate aggregated version for current build
        #  (take all from repo and all of its dependencies)
        build_requirements_path = self._path('requirements.txt')
        if os.path.exists(build_requirements_path):
            os.remove(build_requirements_path)
        with open(build_requirements_path, 'wb') as build_requirements_fp:
            for commit in commits or self._get_all_commit():
                commit_requirements_path = commit._source_path('requirements.txt')
                if commit.repo.use_requirements_txt \
                        and os.path.exists(commit_requirements_path):
                    commit_info = '\n# %s: %s\n' % (commit.repo._get_repo_name_part(), commit.sha)
                    build_requirements_fp.write(commit_info.encode('utf-8'))
                    with open(commit_requirements_path, 'rb') as commit_requirements_fp:
                        shutil.copyfileobj(commit_requirements_fp, build_requirements_fp)

        for commit in commits or self._get_all_commit():
            if commit.repo != self.repo_id:
                continue
            build_export_path = self._docker_source_folder(commit)
            if build_export_path in exports and 'upgrades' not in exports:
                if os.path.exists(commit._source_path('upgrades')):
                    # repository using custom auto-ugrade mecanisms need
                    # 'upgrades' folder to be present at /data/build/upgrades
                    exports['upgrades'] = os.path.join(exports[build_export_path], 'upgrades')

        return exports

    def _cmd(self, python_params=None, py_version=None, local_only=True):
        cmd = super(runbot_build, self)._cmd(python_params=python_params,
                                             py_version=py_version,
                                             local_only=local_only)

        # override command requirements.txt path if build has its own
        if os.path.isfile(self._path('requirements.txt')):
            for pre in cmd.pres:
                for i, arg in enumerate(pre):
                    if 'requirements.txt' in arg:
                        pre[i] = '/data/build/requirements.txt'

        if self.repo_id.custom_config_template:
            with open(self._path('build.conf'), 'w+') as config_file:
                config_file.write("[options]\n")
                config_file.write(self.repo_id.custom_config_template)
            cmd += ["-c", "/data/build/build.conf"]

        return cmd

    def _github_status_notify_all(self, status):
        if status.get('context') == 'ci/runbot':
            status['context'] = self.config_id.github_context or "ci/runbot"
        return super(runbot_build, self)._github_status_notify_all(status)