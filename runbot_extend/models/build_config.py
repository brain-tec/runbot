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

def regex_match_file(filename, pattern):
    regexp = re.compile(pattern)
    with open(filename, 'r') as f:
        if regexp.findall(f.read()):
            return True
    return False

_logger = logging.getLogger(__name__)

class ConfigStep(models.Model):
    _inherit = 'runbot.build.config.step'

    job_type = fields.Selection(
        selection_add=[
            ('restore','Restore Database'),
            ('upgrade', 'Upgrade Database'),
            ])

    def _run_step(self, build, log_path):
        if self.job_type == 'restore':
            return self._restore_db(build, log_path)
        elif self.job_type == 'upgrade':
            return self._upgrade_db(build, log_path)
        return super(ConfigStep, self)._run_step(build, log_path)

    def _post_install_command(self, build, modules_to_install):
        if not build.repo_id.custom_coverage:
            return super(ConfigStep, self)._post_install_command(build, modules_to_install)
        if self.coverage:
            py_version = get_py_version(build)
            # prepare coverage result
            cov_path = build._path('coverage')
            os.makedirs(cov_path, exist_ok=True)
            cmd = [
                '&&', py_version, "-m", "coverage", "html", "-d", "/data/build/coverage", "--include %s" % build.repo_id.custom_coverage,
                "--omit *__openerp__.py,*__manifest__.py",
                "--ignore-errors"
            ]
            return cmd
        return []

    def _coverage_params(self, build, modules_to_install):
        if not build.repo_id.custom_coverage:
            return super(ConfigStep, self)._coverage_params(build, modules_to_install)

        paths = set([mod.strip() for mod in build.repo_id.custom_coverage.split(',')])

        available_modules = [  # todo extract this to build method
            os.path.basename(os.path.dirname(a))
            for a in (glob.glob(build._server('addons/*/__openerp__.py')) + glob.glob(build._server('addons/*/__manifest__.py')))
        ]

        modules_to_analyze = []
        for path in paths:
            modules_to_analyze += [  # todo extract this to build method
                os.path.basename(os.path.dirname(a))
                for a in (glob.glob(build._server('%s/__openerp__.py' % path)) + glob.glob(build._server('%s/__manifest__.py' % path)))
            ]
        module_to_omit = set(available_modules) - set(modules_to_analyze)
        return ['--omit', ','.join('*addons/%s/*' % m for m in module_to_omit) + '*,__manifest__.py']


    def _restore_db(self, build, log_path):
        if not build.db_to_restore:
            return
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
        cmd = ['createdb %s' % db_name]
        cmd += ['&&', 'unzip %s/%s -d %s' % ('data/build/additional_volume', filename, 'data/build/datadir')]
        cmd += ['&&', 'psql -a %s < %s' % (db_name, 'data/build/datadir/dump.sql')]
        return docker_run(' '.join(cmd), log_path, build._path(), build._get_docker_name(), additional_volume=folder_to_add)

    def _upgrade_db(self, build, log_path):
        if not build.db_to_restore:
            return
        ordered_step = self._get_ordered_step(build)
        to_test = build.modules if build.modules and not build.repo_id.force_update_all else 'all'
        cmd, mods = build._cmd()
        db_name = "%s-%s" % (build.dest, self.db_name)
        build._log('upgrade', 'Start Upgrading %s modules on %s' % (to_test, db_name))
        cmd += ['-d', db_name, '-u', to_test, '--stop-after-init', '--log-level=info']
        if build.repo_id.testenable_restore:
            cmd.append("--test-enable")
            if self.test_tags:
                test_tags = self.test_tags.replace(' ', '')
                cmd.extend(['--test-tags', test_tags])
        if self.extra_params:
            cmd.extend(shlex.split(self.extra_params))
        if ordered_step.custom_config_template:
            with open(build._path('build.conf'), 'w+') as config_file:
                config_file.write("[options]\n")
                config_file.write(ordered_step.custom_config_template)
            cmd.extend(["-c", "/data/build/build.conf"])
        return docker_run(build_odoo_cmd(cmd), log_path, build._path(), build._get_docker_name())

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
