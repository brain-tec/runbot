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

# from odoo.addons.runbot.models.build import runbot_job, _re_error, _re_warning, re_job
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
        shutil.copyfile(attachment._full_path(attachment.store_fname), build._path('dump.zip'))
        cmd = ['createdb %s' % db_name]
        cmd += ['&&', 'unzip %s -d %s' % ('data/build/dump.zip', 'data/build/datadir')]
        cmd += ['&&', 'psql -a %s < %s' % (db_name, 'data/build/datadir/dump.sql')]
        return docker_run(' '.join(cmd), log_path, build._path(), build._get_docker_name())

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
        if self._get_ordered_step(build).is_custom_parsing:
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

class runbot_repo(models.Model):
    _inherit = "runbot.repo"

    no_build = fields.Boolean(default=False)
    restored_db = fields.Binary(string='Database to restore (zip)', help='Zip file containing an sql dump and a filestore', attachment=True)
    restored_db_filename = fields.Char()
    force_update_all = fields.Boolean('Force Update ALL', help='Force update all on restore otherwise it will update only the modules in the repository', default=False)
    testenable_restore = fields.Boolean('Test enable on upgrade', help='test enabled on update of the restored database', default=False)
    custom_coverage = fields.Char(string='Custom coverage repository',
                                  help='Use --include arg on coverage: list of file name patterns, for example *addons/module1*,*addons/module2*. It only works on sticky branches on nightly coverage builds.')
    # is_custom_config = fields.Boolean('Use Custom configuration')
    # custom_config = fields.Text('Custom configuration', help = 'This config will be placed in a text file when job_19, behind the [option] line, and passed with a -c to the jobs.')

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

class runbot_build(models.Model):
    _inherit = "runbot.build"

    db_to_restore = fields.Boolean(string='Database to restore')

    def create(self, vals):
        build_id = super(runbot_build, self).create(vals)
        if build_id.repo_id.restored_db:
            build_id.write({'db_to_restore': True})
        return build_id

    # @runbot_job('testing', 'running')
    # def _job_19_custom_config(self, build, log_path):
    #     if not build.repo_id.is_custom_config:
    #         return
    #     cpu_limit = 2400
    #     self._local_pg_createdb("%s-custom_config" % build.dest)
    #     cmd, mods = build._cmd()
    #     build._log('custom_config', 'Start custom config')
    #     if build.repo_id.custom_config:
    #         rbc = self.env['runbot.build.configuration'].create({
    #             'name': 'custom config build %s' % build.id,
    #             'model': 'runbot.build',
    #             'type': 'qweb',
    #             'arch': "<?xml version='1.0'?><t t-name='runbot.build_config_%s'>%s</t>" % (build.id, build.repo_id.custom_config),
    #         })
    #         settings = {'build': build}
    #         build_config = rbc.render(settings)
    #         with open("%s/build.cfg" % build._path(), 'w+') as cfg:
    #             cfg.write("[options]\n")
    #             cfg.write(build_config)
    #         cmd += ['-d', '%s-custom_config' % build.dest, '-c', '%s/build.cfg' % build._path()]
    #     if build.coverage:
    #         cpu_limit *= 1.5
    #         cmd = [get_py_version(build), '-m', 'coverage', 'run',
    #                '--branch', '--source', '/data/build'] + cmd
    #     # reset job_start to an accurate job_19 job_time
    #     build.write({'job_start': now()})
    #     return docker_run(build_odoo_cmd(cmd), log_path, build._path(), build._get_docker_name(), cpu_limit=cpu_limit)


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
