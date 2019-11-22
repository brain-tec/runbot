# -*- coding: utf-8 -*-
import contextlib
import logging
import os
import pwd
import psycopg2
import subprocess

from odoo import models, fields, api
from ..container import Command, docker_run, docker_ps


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

    name = fields.Char('Name', required=True)
    addon = fields.Char('Addon', required=True)
    target_db_name = fields.Char('Target db', required=True)
    project_id = fields.Many2one('runbot_migra.project', required=True)
    version_src = fields.Char('Migration Source Version', required=True)
    build_dir = fields.Char(compute='_get_build_dir', store=False, readonly=True)
    server_dir = fields.Char(compute='_get_server_dir', store=False, readonly=True)
    migration_scripts_dir = fields.Char(compute='_get_migration_scripts_dir', store=False, readonly=True)
    logs_dir = fields.Char(compute='_get_logs_dir', store=False, readonly=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('init', 'Initializing'),
        ('migrate', 'Testing migration'),
        ('done', 'Done')
    ], default='pending', required=True)
    container_name = fields.Char('Current container')

    @api.model
    def create(self, vals):
        build = super(Build, self).create(vals)
        build.project_id.server_repo._clone_repo_to(build.server_dir)
        build.project_id.migration_scripts_repo._clone_repo_to(build.migration_scripts_dir)
        os.makedirs(build.logs_dir, exist_ok=True)
        for addon_repo in build.project_id.addons_repo_ids:
            addon_dir = os.path.join(build.build_dir, addon_repo.name.strip('/').split('/')[-1])
            addon_repo._clone_repo_to(addon_dir)
        return build

    @api.depends('name')
    def _get_build_dir(self):
        for build in self:
            static_path = self.env['runbot_migra.repo']._root()
            sanitized_name = self.env['runbot_migra.repo']._sanitized_name(build.name)
            build.build_dir = os.path.join(static_path, 'builds', sanitized_name)

    @api.depends('name')
    def _get_server_dir(self):
        for build in self:
            build.server_dir = os.path.join(build.build_dir, 'server')

    @api.depends('name')
    def _get_logs_dir(self):
        for build in self:
            build.logs_dir = os.path.join(build.build_dir, 'logs')

    @api.depends('name')
    def _get_migration_scripts_dir(self):
        for build in self:
            build.migration_scripts_dir = os.path.join(build.build_dir, 'scripts')

    @staticmethod
    def _db_exists(dbname):
        with local_pgadmin_cursor() as local_cr:
            local_cr.execute("""SELECT datname FROM pg_database WHERE datname='%s';""" % dbname)
            res = local_cr.fetchone()
            return res

    def _get_addons_dirs(self):
        self.ensure_one()
        addons_dirs = []
        for addon_repo in self.project_id.addons_repo_ids:
            addon_dir = os.path.join(self.build_dir, addon_repo.name.strip('/').split('/')[-1])
            addons_dirs.append(addon_dir)
        return addons_dirs

    def _checkout_addons(self, version):
        self.ensure_one()
        for addon_repo in self.project_id.addons_repo_ids:
            addon_dir = os.path.join(self.build_dir, addon_repo.name.strip('/').split('/')[-1])
            subprocess.check_output(['git', 'checkout', version], cwd=addon_dir)

    def _checkout(self, version):
        self.ensure_one()
        self._checkout_addons(version)
        subprocess.check_output(['git', 'checkout', version], cwd=self.server_dir)

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
        return free if free > 0 else 0

    def _launch_odoo(self, db_name, modules_to_install, log_path, server_dir, mode='install'):
        self.ensure_one()
        py_version = '3'
        pres = []
        posts = []

        ro_volumes = {os.path.split(a)[-1]: a for a in self._get_addons_dirs()}

        odoo_cmd = ['python%s' % py_version, 'odoo-bin']
        # options
        odoo_cmd += ['--no-http']

        # use the username of the host to connect to the databases
        odoo_cmd += ['-r %s' % pwd.getpwuid(os.getuid()).pw_name]
        odoo_cmd += ['-d', db_name]
        if mode == 'install':
            odoo_cmd += ['-i', modules_to_install]
        elif mode == 'update':
            odoo_cmd += ['-u', 'all']
        odoo_cmd += ['--stop-after-init']
        odoo_cmd += ['--max-cron-threads=0']
        odoo_cmd += ['--addons-path', ','.join(['/data/build/addons'] + ['/data/build/%s' % a for a in ro_volumes.keys()])]

        docker_command = Command(pres, odoo_cmd, posts)

        docker_name = '%s' % db_name
        self.container_name = docker_name
        return docker_run(docker_command.build(), log_path, server_dir, docker_name, ro_volumes=ro_volumes)

    def _init_build(self):
        self.ensure_one()
        # start init phase
        self.state = 'init'
        if not self._db_exists(self.name):
            self._checkout(self.version_src)
            _logger.info('Creating DB %s', self.name)
            log_path = os.path.join(self.build_dir, 'logs', 'create_%s.txt' % self.name)
            self._launch_odoo(self.name, 'base,%s' % self.addon, log_path, self.server_dir)

    def _migrate_build(self):
        self.ensure_one()
        _logger.info('Migrating DB %s', self.name)
        self._checkout(self.project_id.version_target)
        log_path = os.path.join(self.build_dir, 'logs', 'update_%s.txt' % self.name)
        self.state = 'migrate'
        self._launch_odoo(self.name, 'base,%s' % self.addon, log_path, self.server_dir, mode='update')

    @api.model
    def _process_build_queue(self):
        for pending_build in self.search([('state', '=', 'pending')], limit=self._get_free_docker_slots()):
            try:
                pending_build._init_build()
            except Exception:
                _logger.info('Init Build failed: %s', pending_build.name)

        for init_build in self.search([('state', '=', 'init')], limit=self._get_free_docker_slots()):
            try:
                init_build._migrate_build()
            except Exception:
                _logger.info('Migrate Build failed: %s', init_build.name)