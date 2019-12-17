# -*- coding: utf-8 -*-

import logging
import os
import subprocess

from odoo import models, fields, api


_logger = logging.getLogger(__name__)


class Version(models.Model):

    _name = "runbot_migra.version"
    _description = "Odoo versions"

    name = fields.Char('Version')
    project_ids = fields.Many2many('runbot_migra.project', string='Projects')


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
    version_ids = fields.Many2many('runbot_migra.version', string='Version Tags')
    build_ids = fields.One2many('runbot_migra.build', 'project_id', string='Builds')

    def _update_repos(self):
        self.ensure_one()
        repos = self.server_repo | self.addons_repo_ids | self.migration_scripts_repo
        for repo in repos:
            repo._update_git()

    def _reset_worktrees(self):
        self.ensure_one()
        # rebase servers worktrees
        for worktree in [d for d in os.scandir(self.servers_dir) if d.is_dir()]:
            _logger.info('resetting server worktree %s', worktree.path)
            cmd = ['git', 'reset', '--hard', worktree.name]
            subprocess.check_output(cmd, cwd=worktree.path)

        # rebase addons worktrees
        for addon_path in [ap.path for ap in os.scandir(self.addons_dir) if ap.is_dir()]:
            for worktree in [d for d in os.scandir(addon_path) if d.is_dir()]:
                _logger.info('ressetting addon worktree %s', worktree.path)
                cmd = ['git', 'reset', '--hard', worktree.name]
                subprocess.check_output(cmd, cwd=worktree.path)

        # rebase migrations scripts
        _logger.info('ressetting migration script worktree %s', self.migration_scripts_dir)
        cmd = ['git', 'reset', '--hard', self.migration_scripts_branch]
        subprocess.check_output(cmd, cwd=self.migration_scripts_dir)

    @api.depends('name')
    def _get_project_dir(self):
        for project in self:
            static_path = self.env['runbot_migra.repo']._root()
            sanitized_name = self.env['runbot_migra.repo']._sanitized_name(project.name)
            project.project_dir = os.path.join(static_path, 'projects', sanitized_name)

    @api.depends('name', 'project_dir')
    def _get_servers_dir(self):
        for project in self:
            project.servers_dir = os.path.join(project.project_dir, 'servers')

    @api.depends('name', 'project_dir')
    def _get_addons_dir(self):
        for project in self:
            project.addons_dir = os.path.join(project.project_dir, 'addons')

    @api.depends('name', 'project_dir')
    def _get_migration_scripts_dir(self):
        for project in self:
            project.migration_scripts_dir = os.path.join(project.project_dir, 'scripts')

    @staticmethod
    def _list_addons_from_dir(addons_path):
        """ yield a list of dirs in path """
        if not os.path.exists(addons_path):
            _logger.warning('addons path "%s" not found', addons_path)
            return
        for f in os.listdir(addons_path):
            if not f.startswith('.') and os.path.isdir(os.path.join(addons_path, f)):
                yield f

    def _get_hashes(self):

        def rev_parse(git_dir):
            if not git_dir.endswith('.git'):
                git_dir = os.path.join(git_dir, '.git')
            cmd = ['git', '--git-dir=%s' % git_dir, 'rev-parse', 'HEAD']
            try:
                res = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()
            except subprocess.CalledProcessError:
                res = 'Not a git dir'
            return res

        self.ensure_one()
        hashes = {}
        git_dir = self.migration_scripts_dir
        hashes[git_dir] = rev_parse(git_dir)

        for version in [self.version_target] + self.version_ids.mapped('name'):
            git_dir = os.path.join(self.servers_dir, version)
            hashes[git_dir] = rev_parse(git_dir)

            for addon_repo in self.addons_repo_ids:
                addon_dirname = addon_repo.name.strip('/').split('/')[-1]
                addon_path = os.path.join(self.addons_dir, addon_dirname)
                git_dir = os.path.join(addon_path, version)
                hashes[git_dir] = rev_parse(git_dir)
        return hashes

    def _get_addons(self, version):
        self.ensure_one()
        addons = []
        # server addons
        addons.extend(self._list_addons_from_dir(os.path.join(self.servers_dir, version, 'addons')))
        # other addons
        for addon_repo in self.addons_repo_ids:
            addon_dirname = addon_repo.name.strip('/').split('/')[-1]
            addons.extend(self._list_addons_from_dir(os.path.join(self.addons_dir, addon_dirname, version)))
        return addons

    def _test_upgrade(self):
        """ Create upgrade builds for a project """
        self.ensure_one()

        # ensure builds are done and clean previous builds
        if self.build_ids.filtered(lambda rec: rec.state != 'done'):
            return
        self.build_ids.filtered(lambda rec: rec.state == 'done')._clean()
        self.build_ids.unlink()

        # fetch repos
        self._update_repos()

        # add worktrees if needed
        for version in [self.version_target] + self.version_ids.mapped('name'):
            self.server_repo._add_worktree(os.path.join(self.servers_dir, version), version)

        self.migration_scripts_repo._add_worktree(self.migration_scripts_dir, self.migration_scripts_branch)

        for addon_repo in self.addons_repo_ids:
            addon_dirname = addon_repo.name.strip('/').split('/')[-1]
            for version in [self.version_target] + self.version_ids.mapped('name'):
                addon_path = os.path.join(self.addons_dir, addon_dirname)
                addon_repo._add_worktree(os.path.join(addon_path, version), version)

        self._reset_worktrees()

        # create a broken symlink
        symlink_target = os.path.join(self.servers_dir, self.version_target, 'odoo/addons/base/maintenance')
        if not os.path.islink(symlink_target):
            os.symlink('/data/build/migration_scripts',  symlink_target)

        addons = self._get_addons(self.version_target)

        # #### TO REMOVE ####
        # addons = addons[:8]  # LIMIT TO 4 ADDONS
        # ###################

        for version in [v.strip() for v in self.version_ids.mapped('name')]:
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
