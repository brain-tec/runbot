# -*- coding: utf-8 -*-
import contextlib
import fnmatch
import logging
import os
import pwd
import psycopg2
import subprocess

from odoo import models, fields, api
from ..container import Command, docker_run, docker_ps, docker_is_running, docker_build


_logger = logging.getLogger(__name__)


@contextlib.contextmanager
def local_pgadmin_cursor():
    cnx = None
    try:
        cnx = psycopg2.connect("dbname=postgres")
        cnx.autocommit = True  # required for admin commands
        yield cnx.cursor()
    finally:
        if cnx:
            cnx.close()


class Build(models.Model):

    _name = "runbot_migra.build"
    _description = "Migration build"
    _order = "addon"

    name = fields.Char('Name', required=True)
    addon = fields.Char('Addon', required=True)
    target_db_name = fields.Char('Target db', required=True)
    project_id = fields.Many2one('runbot_migra.project', required=True)
    version_src = fields.Char('Migration Source Version', required=True)
    build_dir = fields.Char(compute='_get_build_dir', store=False, readonly=True)
    logs_dir = fields.Char(compute='_get_logs_dir', store=False, readonly=True)
    log_update = fields.Char(compute='_get_log_update_path', store=False, readonly=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('base', 'Install base'),
        ('addon', 'Install addon'),
        ('migrate', 'Testing migration'),
        ('done', 'Done')
    ], default='pending', required=True)
    result = fields.Selection([
        ('ok', 'Success'),
        ('ko', 'Failure')
    ])
    container_name = fields.Char('Current container')
    template_db = fields.Char('Template Database')

    @api.model
    def create(self, vals):
        build = super(Build, self).create(vals)
        os.makedirs(build.logs_dir, exist_ok=True)
        os.makedirs(os.path.join(build.build_dir, 'addons'), exist_ok=True)
        return build

    @api.depends('name')
    def _get_build_dir(self):
        for build in self:
            static_path = self.env['runbot_migra.repo']._root()
            sanitized_name = self.env['runbot_migra.repo']._sanitized_name(build.name)
            build.build_dir = os.path.join(static_path, 'builds', sanitized_name)

    @api.depends('build_dir')
    def _get_logs_dir(self):
        for build in self:
            build.logs_dir = os.path.join(build.build_dir, 'logs')

    @api.depends('name', 'build_dir')
    def _get_log_update_path(self):
        for build in self:
            build.log_update = os.path.join(build.build_dir, 'logs', 'update_%s.txt' % build.name)

    @staticmethod
    def _git_rev_parse(git_dir, treeish):
        return subprocess.check_output(['git', '--git-dir=%s' % git_dir, 'rev-parse', treeish]).decode().strip()

    @staticmethod
    def _db_exists(dbname):
        with local_pgadmin_cursor() as local_cr:
            local_cr.execute("""SELECT datname FROM pg_database WHERE datname='%s';""" % dbname)
            res = local_cr.fetchone()
            return res

    def _get_addons_dirs(self, version):
        self.ensure_one()
        addons_dirs = []
        for addon_repo in self.project_id.addons_repo_ids:
            addon_dir = os.path.join(self.project_id.addons_dir, addon_repo.name.strip('/').split('/')[-1], version)
            addons_dirs.append(addon_dir)
        return addons_dirs

    def _checkout_addons(self, version):
        self.ensure_one()
        for addon_repo in self.project_id.addons_repo_ids:
            addon_dir = os.path.join(self.build_dir, addon_repo.name.strip('/').split('/')[-1])
            subprocess.check_output(['git', 'checkout', version], cwd=addon_dir)

    def _dropdb(self, dbname):
        with local_pgadmin_cursor() as local_cr:
            pid_col = 'pid' if local_cr.connection.server_version >= 90200 else 'procpid'
            query = 'SELECT pg_terminate_backend({}) FROM pg_stat_activity WHERE datname=%s'.format(pid_col)
            local_cr.execute(query, [dbname])
            local_cr.execute('DROP DATABASE IF EXISTS "%s"' % dbname)

    def _createdb(self, dbname, template=''):
        template_name = template if template else 'template0'
        self._dropdb(dbname)
        _logger.debug("createdb %s", dbname)
        with local_pgadmin_cursor() as local_cr:
            local_cr.execute("""CREATE DATABASE "%s" TEMPLATE "%s" LC_COLLATE 'C' ENCODING 'unicode'""" % (dbname, template_name))

    def _get_free_docker_slots(self):
        max_running = int(self.env['ir.config_parameter'].get_param('runbot_migra.max_running', 4))
        running_dockers = [docker_name for docker_name in docker_ps() if '-upddb-' in docker_name]
        free = max_running - len(running_dockers)
        return free if free > 0 else 0.1

    def _clean(self):
        for build in self:
            for f in fnmatch.filter(os.listdir(build.logs_dir), '*%s.txt*' % build.name):
                os.unlink(os.path.join(build.logs_dir, f))

    def build_docker_container(self):
        self.ensure_one()
        try:
            _logger.info('Building docker image')
            log_path = os.path.join(self.logs_dir, 'docker_build.txt')
            docker_build(log_path, self.build_dir)
        except Exception:
            _logger.exception('Failed to build docker containe')

    def _launch_odoo(self, db_name, modules_to_install, log_path, version):
        self.ensure_one()
        py_version = '3'
        pres = []
        posts = []

        self.build_docker_container()

        ro_volumes = {'addons/%s' % '/'.join(a.split('/')[-2:-1]): a for a in self._get_addons_dirs(version)}

        odoo_cmd = ['python%s' % py_version, 'odoo/odoo-bin']
        # options
        odoo_cmd += ['--no-http']

        # use the username of the host to connect to the databases
        odoo_cmd += ['-r %s' % pwd.getpwuid(os.getuid()).pw_name]
        odoo_cmd += ['-d', db_name]
        if self.state in ('base', 'addon'):
            odoo_cmd += ['-i', modules_to_install]
        elif self.state == 'migrate':
            odoo_cmd += ['-u', 'all',
                         '--db-filter=^%s$' % db_name,
                         '--log-handler=:INFO', '--log-handler=odoo.models.schema:INFO', '--log-handler=odoo.modules.loading:DEBUG',
                         '--log-handler=odoo.modules.graph:CRITICAL', '--log-handler=odoo.modules.migration:DEBUG', ' --log-handler=odoo.tools.misc:INFO',
                         '--log-handler=odoo.addons.base.maintenance.migrations:DEBUG']

        odoo_cmd += ['--stop-after-init']
        odoo_cmd += ['--max-cron-threads=0']
        odoo_cmd += ['--addons-path', ','.join(['/data/build/odoo/addons'] + ['%s' % k for k in ro_volumes.keys()])]

        # adding odoo server
        server_dir = os.path.join(self.project_id.servers_dir, version)
        ro_volumes.update({'odoo': server_dir})

        # adding migration scripts
        if self.state == 'migrate':
            ro_volumes.update({'migration_scripts': self.project_id.migration_scripts_dir})

        # update res_partner when l10_n
        if self.addon.startswith('l10n_') and self.state == 'addon' and self.template_db:
            _logger.info('Changing res_partner country in  DB %s', db_name)
            country_code = self.addon.split('_')[1]
            pres = [
                ['psql', '-d', '"%s"' % self.template_db, '-c', "\"UPDATE res_partner SET country_id = (SELECT id FROM res_country WHERE lower(code)='%s')\"" % country_code,
                 '-U', pwd.getpwuid(os.getuid()).pw_name],
            ]

        docker_command = Command(pres, odoo_cmd, posts)

        self.container_name = '%s-%s' % (db_name, self.state)

        # write commit hashes in log file
        with open('%s.hashes' % log_path, 'w') as lf:
            for k, v in ro_volumes.items():
                hash = self._git_rev_parse(os.path.join(v, '.git'), 'HEAD')
                lf.write("commit for %s: %s\n" % (k, hash))

        # pre-create database if template is specified
        if self.template_db:
            self._createdb(db_name, template=self.template_db)
        return docker_run(docker_command.build(), log_path, self.build_dir, self.container_name, ro_volumes=ro_volumes)

    def _base_build(self):
        self.ensure_one()
        # start init phase
        self.state = 'base'
        db_name = '%s-base' % self.name
        if not self._db_exists(db_name):
            _logger.info('Creating DB %s', db_name)
            log_path = os.path.join(self.build_dir, 'logs', 'base_%s.txt' % self.name)
            self._launch_odoo(db_name, 'base', log_path, self.version_src)

    def _install_addon_build(self):
        # ensure that install is finished
        if self.container_name and docker_is_running(self.container_name):
            return
        self.state = 'addon'
        self.template_db = '%s-base' % self.name
        self._dropdb(self.name)
        log_path = os.path.join(self.build_dir, 'logs', 'addon_%s.txt' % self.name)
        self._launch_odoo(self.name, self.addon, log_path, self.version_src)

    def _migrate_build(self):
        self.ensure_one()
        _logger.info('Migrating DB %s', self.name)
        # ensure that install is finished
        if self.container_name and docker_is_running(self.container_name):
            return
        self.state = 'migrate'
        self.template_db = False
        self._launch_odoo(self.name, None, self.log_update, self.project_id.version_target)

    def _finish_build(self):
        self.ensure_one()
        if not docker_is_running(self.container_name):
            self.state = 'done'
            log_content = open(self.log_update, 'r').read()
            if 'Modules loaded.' in log_content:
                self.result = 'ok'
            else:
                self.result = 'ko'

    @api.model
    def _process_build_queue(self):
        for pending_build in self.search([('state', '=', 'pending')], limit=self._get_free_docker_slots()):
            try:
                pending_build._base_build()
            except Exception:
                _logger.error('Init Build failed: %s', pending_build.name)
                raise

        for init_build in self.search([('state', '=', 'base')], limit=self._get_free_docker_slots()):
            try:
                init_build._install_addon_build()
            except Exception:
                _logger.error('Install Addon Build failed: %s', init_build.name)
                raise

        for init_build in self.search([('state', '=', 'addon')], limit=self._get_free_docker_slots()):
            try:
                init_build._migrate_build()
            except Exception:
                _logger.error('Migrate Build failed: %s', init_build.name)
                raise

        for init_build in self.search([('state', '=', 'migrate')]):
            try:
                init_build._finish_build()
            except Exception:
                _logger.info('Finish Build failed: %s', init_build.name)
                raise
