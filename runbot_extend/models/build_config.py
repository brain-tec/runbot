# -*- encoding: utf-8 -*-

import glob
import io
import fnmatch
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
from odoo.exceptions import UserError
from odoo.addons.runbot.container import docker_build, docker_run
from odoo.addons.runbot.models.build_config import _re_error, _re_warning
from odoo.addons.runbot.common import dt2time, fqdn, now, grep, time2str, rfind, uniq_list, local_pgadmin_cursor

def regex_match_file(filename, pattern):
    regexp = re.compile(pattern)
    with open(filename, 'r') as f:
        if regexp.findall(f.read()):
            return True
    return False

_logger = logging.getLogger(__name__)

class Config(models.Model):
    _inherit = "runbot.build.config"

    github_context = fields.Char('Github context', default='ci/runbot')

class ConfigStep(models.Model):
    _inherit = 'runbot.build.config.step'

    job_type = fields.Selection(
        selection_add=[
            ('restore','Restore Database'),
            ('upgrade', 'Upgrade Database'),
            ])

    def _check_step_ids_order(self):
        """Ensure step are correctly ordered.

        ..note: Customized to allow either an 'install_odoo' or a 'restore' job
            before an 'run_odoo' job.
        """
        install_job = False
        restore_job = False
        step_ids = self.step_ids()
        for step in step_ids:
            if step.job_type == 'install_odoo':
                install_job = True
            if step.job_type == 'restore':
                restore_job = True
            if step.job_type == 'run_odoo':
                if step != step_ids[-1]:
                    raise UserError('Jobs of type run_odoo should be the last one')
                if not install_job and not restore_job:
                    raise UserError('Jobs of type run_odoo should be preceded by a job of type install_odoo')
        self._check_recustion()

    def _run_step(self, build, log_path):
        if self.job_type == 'restore':
            return self._restore_db(build, log_path)
        elif self.job_type == 'upgrade':
            return self._upgrade_db(build, log_path)
        return super(ConfigStep, self)._run_step(build, log_path)

    def _post_install_command(self, build, modules_to_install, py_version=None):
        if not build.repo_id.custom_coverage:
            return super(ConfigStep, self)._post_install_command(build, modules_to_install)
        if self.coverage:
            py_version = py_version if py_version is not None else build._get_py_version()
            # prepare coverage result
            cov_path = build._path('coverage')
            os.makedirs(cov_path, exist_ok=True)
            include_dirs = build.repo_id.custom_coverage.replace(r'"', r'\"').strip()
            cmd = [
                'python%s' % py_version, "-m", "coverage", "html", "-d", "/data/build/coverage", '--include "%s"' % include_dirs,
                "--omit *__openerp__.py,*__manifest__.py",
                "--ignore-errors"
            ]
            return cmd
        return []

    def _coverage_params(self, build, modules_to_install):
        if not build.repo_id.custom_coverage:
            return super(ConfigStep, self)._coverage_params(build, modules_to_install)

        paths = set([mod.strip() for mod in build.repo_id.custom_coverage.split(',')])
        pattern_to_omit = set()

        # omit all modules except those matching the 'custom_coverage' patterns
        for commit in build._get_all_commit():
            docker_source_folder = build._docker_source_folder(commit)
            for manifest_file in (commit.repo.manifest_files or '').split(','):
                pattern_to_omit.add('*%s' % manifest_file)
            for (addons_path, module, _) in build._get_available_modules(commit):
                # we want to omit docker_source_folder/[addons/path/]module/*
                module_path_in_docker = os.path.join(docker_source_folder, addons_path, module)
                if not any(fnmatch.fnmatch(module_path_in_docker, path) for path in paths):
                    pattern_to_omit.add('%s/*' % (module_path_in_docker))

        return ['--omit', ','.join(pattern_to_omit)]


    def _restore_db_from_zip(self, build):
        db_name = '%s-%s' % (build.dest, self.db_name)
        build._log('restore', 'Restoring database on %s' % db_name)
        os.makedirs(build._path('temp'), exist_ok=True)
        attachment = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', build.repo_id._name),
            ('res_field', '=', 'restored_db'),
            ('res_id', 'in', build.repo_id.ids),
        ], limit=1)
        folder, filename = attachment.store_fname.split('/')
        folder_to_add = os.path.join(attachment._filestore(), folder)
        folder_to_restore = '/data/build/datadir/filestore/%s' % db_name
        restore_volumes = {'restore_volume': folder_to_add}

        #
        # restore.zip layout is like:
        #   - filestore/*/*
        #   - manifest.json
        #   - dump.sql
        #
        # 1. filestore content will be moved to datadir/filestore/{db_name}/*
        cmd = ['createdb %s' % db_name]
        cmd += ['&&', 'mkdir -p %s' % folder_to_restore]
        cmd += ['&&', 'unzip %s/%s -d %s' % ('/data/build/restore_volume', filename, folder_to_restore)]
        cmd += ['&&', 'mv %s/filestore/* %s' % (folder_to_restore, folder_to_restore)]
        # 2. dump.sql is restored to {db_name}
        cmd += ['&&', 'psql -a %s < %s/%s' % (db_name, folder_to_restore, 'dump.sql')]
        cmd += ['&&', 'rm -rf %s' % folder_to_restore]
        return cmd, restore_volumes

    def _restore_db_from_template(self, build):
        db_name = '%s-%s' % (build.dest, self.db_name)
        template_db_name = build.repo_id.template_db_name
        build._log('restore', 'Restoring database from %s on %s' % (template_db_name, db_name))
        restore_volumes = {} #TODO
        cmd = ['createdb -T %s %s' % (template_db_name, db_name)]
        return cmd, restore_volumes

    def _restore_db(self, build, log_path):
        if not build.db_to_restore:
            return
        if build.repo_id.template_db_name:
            cmd, restore_volumes = self._restore_db_from_template(build)
        else:
            cmd, restore_volumes = self._restore_db_from_zip(build)
        return docker_run(' '.join(cmd), log_path, build._path(), build._get_docker_name(), ro_volumes=restore_volumes)

    def _upgrade_db(self, build, log_path):
        if not build.db_to_restore:
            return
        exports = build._checkout()
        ordered_step = self._get_ordered_step(build)
        to_test = build.repo_id.modules if build.repo_id.modules and not build.repo_id.force_update_all else 'all'
        cmd = build._cmd()
        db_name = "%s-%s" % (build.dest, self.db_name)
        build._log('upgrade', 'Start Upgrading %s modules on %s' % (to_test, db_name))
        cmd += ['-d', db_name, '-u', to_test, '--stop-after-init', '--log-level=info']
        if build.repo_id.testenable_restore:
            cmd += ["--test-enable"]
            if self.test_tags:
                test_tags = self.test_tags.replace(' ', '')
                cmd += ['--test-tags', test_tags]
        if self.extra_params:
            cmd += shlex.split(self.extra_params)
        if ordered_step.custom_config_template:
            with open(build._path('build.conf'), 'w+') as config_file:
                config_file.write("[options]\n")
                config_file.write(ordered_step.custom_config_template)
            if not build.repo_id.custom_config_template:  # avoid adding twice the command
                cmd += ["-c", "/data/build/build.conf"]
        return docker_run(cmd.build(), log_path, build._path(), build._get_docker_name(), ro_volumes=exports)

    def _make_results(self, build):
        if self and self._get_ordered_step(build).is_custom_parsing:
            return self._make_customized_results(build)
        else:
            return super(ConfigStep, self)._make_results(build)

    def _make_customized_results(self, build):
        ordered_step = self._get_ordered_step(build)
        build_values = {}
        build._log('run', 'Getting results for build %s, analyzing %s.txt' % (build.dest, build.active_step.name))
        log_file = build._path('logs', '%s.txt' % build.active_step.name)
        if not os.path.isfile(log_file):
            build_values['local_result'] = 'ko'
            build._log('_checkout', "Log file not found at the end of test job", level="ERROR")
        else:
            log_time = time.localtime(os.path.getmtime(log_file))
            build_values['job_end'] = time2str(log_time),
            if not build.local_result or build.local_result in ['ok', "warn"]:
                if self.job_type not in ['install_odoo', 'run_odoo', 'upgrade']:
                    if ordered_step.custom_re_error and regex_match_file(log_file, ordered_step.custom_re_error):
                        local_result = 'ko'
                        build._log('_checkout', 'Error or traceback found in logs', level="ERROR")
                    elif ordered_step.custom_re_warning and regex_match_file(log_file, ordered_step.custom_re_warning):
                        local_result = 'warn'
                        build._log('_checkout', 'Warning found in logs', level="WARNING")
                    else:
                        local_result = 'ok'
                else:
                    if rfind(log_file, r'modules\.loading: \d+ modules loaded in'):
                        local_result = False
                        if ordered_step.custom_re_error and rfind(log_file, ordered_step.custom_re_error):
                            local_result = 'ko'
                            build._log('_checkout', 'Error or traceback found in logs', level="ERROR")
                        elif ordered_step.custom_re_warning and rfind(log_file, ordered_step.custom_re_warning):
                            local_result = 'warn'
                            build._log('_checkout', 'Warning found in logs', level="WARNING")
                        elif not grep(log_file, "Initiating shutdown"):
                            local_result = 'ko'
                            build._log('_checkout', 'No "Initiating shutdown" found in logs, maybe because of cpu limit.', level="ERROR")
                        else:
                            local_result = 'ok'
                        build_values['local_result'] = build._get_worst_result([build.local_result, local_result])
                    else:
                        build_values['local_result'] = 'ko'
                        build._log('_checkout', "Module loaded not found in logs", level="ERROR")
        return build_values

    def _get_ordered_step(self, build):
        self.ensure_one()
        return self.env['runbot.build.config.step.order'].search([
            ('config_id', '=', build.config_id.id),
            ('step_id', '=', self.id),
            ], limit=1)

class ConfigStepOrder(models.Model):
    _inherit = 'runbot.build.config.step.order'

    is_custom_parsing = fields.Boolean('Customized parsing', default=False)
    custom_re_error = fields.Char(string='Error Custom Regex', default=_re_error)
    custom_re_warning = fields.Char(string='Warning Custom Regex', default=_re_warning)
    custom_config_template = fields.Text(string='Custom config', help='Custom Config, rendered with qweb using build as the main variable')
    job_type = fields.Selection(related='step_id.job_type')
