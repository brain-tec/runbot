# -*- coding: utf-8 -*-

import logging
import os
import subprocess
from odoo import models, fields, api


_logger = logging.getLogger(__name__)


class Project(models.Model):

    _name = "runbot_migra.project"
    _description = "Migration project"

    name = fields.Char('Name', required=True)
    active = fields.Boolean(default=True)
    server_repo = fields.Many2one('runbot_migra.repo', 'Odoo server repo', required=True)
    addons_repo_ids = fields.Many2many('runbot_migra.repo', string='Additional addons repos')
    migration_scripts_repo = fields.Many2one('runbot_migra.repo', 'Migration scripts repo', required=True)
    migration_scripts_branch = fields.Char(default='master')
    version_target = fields.Char('Targeted version', help='Final version, used by the update instance')
    versions = fields.Char('Start versions', help='Comma separated intermediary versions')
    build_dir = fields.Char(compute='_get_build_dir', store=False, readonly=True)

    @api.depends('name')
    def _get_build_dir(self):
        for project in self:
            static_path = self.env['runbot_migra.repo']._root()
            sanitized_name = self.env['runbot_migra.repo']._sanitized_name(project.name)
            project.build_dir = os.path.join(static_path, 'build', sanitized_name)

    def _update_repos(self):
        self.ensure_one()
        repos = self.server_repo | self.addons_repo_ids | self.migration_scripts_repo
        for repo in repos:
            repo._update_git()

    @staticmethod
    def _get_addons(path):
        """ yield a list of dirs in path """
        for f in os.listdir(path):
            if os.path.isdir(os.path.join(path, f)):
                yield f

    def _test_upgrade(self):
        """ Start project tests upgrade """
        addons = []
        self.ensure_one()
        self._update_repos()

        os.makedirs(self.build_dir, exist_ok=True)
        server_dir = os.path.join(self.build_dir, 'server')
        self.server_repo._sync_branch(server_dir, self.version_target)
        addons.extend(self._get_addons(os.path.join(server_dir, 'addons')))

        migration_scripts_dir = os.path.join(self.build_dir, 'scripts')
        self.migration_scripts_repo._sync_branch(migration_scripts_dir, self.migration_scripts_branch)

        for addon_repo in self.addons_repo_ids:
            addon_dir = os.path.join(self.build_dir, addon_repo.name.strip('/').split('/')[-1])
            addon_repo._sync_branch(addon_dir, self.version_target)
            addons.extend(self._get_addons(addon_dir))

        for 

    @api.model
    def _test_all_upgrade(self):
        """ Start all projects migrations tests"""
        for project in self.env['runbot_migra.project'].search([('active', '=', True)]):
            project._test_upgrade()
