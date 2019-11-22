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
    project_dir = fields.Char(compute='_get_project_dir', store=False, readonly=True)
    servers_dir = fields.Char(compute='_get_servers_dir', store=False, readonly=True)
    addons_dir = fields.Char(compute='_get_addons_dir', store=False, readonly=True)
    addons_repo_ids = fields.Many2many('runbot_migra.repo', string='Additional addons repos')
    migration_scripts_repo = fields.Many2one('runbot_migra.repo', 'Migration scripts repo', required=True)
    migration_scripts_branch = fields.Char(default='master')
    migration_scripts_dir = fields.Char(compute='_get_migration_scripts_dir', store=False, readonly=True)
    version_target = fields.Char('Targeted version', help='Final version, used by the update instance')
    versions = fields.Char('Start versions', help='Comma separated intermediary versions')

    def _update_repos(self):
        self.ensure_one()
        repos = self.server_repo | self.addons_repo_ids | self.migration_scripts_repo
        for repo in repos:
            repo._update_git()

    @api.depends('name')
    def _get_project_dir(self):
        for project in self:
            static_path = self.env['runbot_migra.repo']._root()
            sanitized_name = self.env['runbot_migra.repo']._sanitized_name(project.name)
            project.project_dir = os.path.join(static_path, 'projects', sanitized_name)

    @api.depends('name')
    def _get_servers_dir(self):
        for project in self:
            project.servers_dir = os.path.join(project.project_dir, 'servers')

    @api.depends('name')
    def _get_addons_dir(self):
        for project in self:
            project.servers_dir = os.path.join(project.project_dir, 'addons')

    @api.depends('name')
    def _get_migration_scripts_dir(self):
        for project in self:
            project.migration_scripts_dir = os.path.join(project.project_dir, 'scripts')

    @staticmethod
    def _get_addons(addons_path):
        """ yield a list of dirs in path """
        for f in os.listdir(addons_path):
            if os.path.isdir(os.path.join(addons_path, f)):
                yield f

    def _test_upgrade(self):
        """ Create upgrade builds for a project """
        addons = []
        self.ensure_one()
        self._update_repos()

        # add worktrees if needed
        for version in [self.version_target] + self.versions.split(','):
            self.server_repo._add_worktree(os.path.join(self.servers_dir, version), version)

        print('FINI')
        return
        for addon_repo in self.addons_repo_ids:
            addon_dir = os.path.join(self.project_dir, addon_repo.name.strip('/').split('/')[-1])
            addon_repo._clone_repo_to(addon_dir)
            subprocess.check_output(['git', 'checkout', self.version_target], cwd=addon_dir)
            addons.extend(self._get_addons(addon_dir))

        addons.extend(self._get_addons(os.path.join(self.servers_dir, 'addons')))

        # #### TO REMOVE ####
        addons = addons[:8]  # LIMIT TO 4 ADDONS
        # ###################

        for version in [v.strip() for v in self.versions.strip().split(',')]:
            for addon in addons:
                src_db_name = '%s-upddb-%s' % (version, addon)
                target_db_name = '%s-%s-upddb-%s' % (self.version_target, version, addon)
                build = self.env['runbot_migra.build'].search([('name', '=', src_db_name)])
                # recycle done build or let it finish
                if build:
                    if build.state == 'done':
                        build.state = 'pending'
                    else:
                        continue
                else:
                    self.env['runbot_migra.build'].create({
                        'name': target_db_name,
                        'version_src': version,
                        'target_db_name': target_db_name,
                        'project_id': self.id,
                        'addon': addon,
                        'state': 'pending'
                    })

    @api.model
    def _test_all_upgrade(self):
        """ Start all projects migrations tests"""
        for project in self.env['runbot_migra.project'].search([('active', '=', True)]):
            project._test_upgrade()
