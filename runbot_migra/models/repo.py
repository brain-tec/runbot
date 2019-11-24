# -*- coding: utf-8 -*-

import logging
import os
import re
import subprocess

from odoo import models, fields, api


_logger = logging.getLogger(__name__)


class Repo(models.Model):

    _name = "runbot_migra.repo"
    _description = "Github repository"

    name = fields.Char('Repository', required=True)
    path = fields.Char(compute='_get_path', string='Directory', readonly=True)
    short_name = fields.Char('Repo', compute='_compute_short_name', store=False, readonly=True)
    base = fields.Char(compute='_get_base_url', string='Base URL', readonly=True)

    @api.model
    def _sanitized_name(self, name):
        for i in '@:/':
            name = name.replace(i, '_')
        return name

    def _root(self):
        """Return root directory of repository"""
        default = os.path.join(os.path.dirname(__file__), '../static')
        return os.path.abspath(default)

    @api.depends('name')
    def _get_path(self):
        """compute the server path of repo from the name"""
        root = self._root()
        for repo in self:
            repo.path = os.path.join(root, 'repos', repo._sanitized_name(repo.name))

    @api.depends('name')
    def _get_base_url(self):
        for repo in self:
            if not repo.name:
                repo.base = ''
            else:
                name = re.sub('.+@', '', repo.name)
                name = re.sub('^https://', '', name)  # support https repo style
                name = re.sub('.git$', '', name)
                name = name.replace(':', '/')
                repo.base = name

    @api.depends('name', 'base')
    def _compute_short_name(self):
        for repo in self:
            repo.short_name = '/'.join(repo.base.split('/')[-2:])

    def _git(self, cmd):
        """Execute a git command 'cmd'"""
        self.ensure_one()
        _logger.debug("git command: git (dir %s) %s", self.short_name, ' '.join(cmd))
        cmd = ['git', '--git-dir=%s' % self.path] + cmd
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()

    def _clone(self):
        """ Clone the remote repo if needed """
        self.ensure_one()
        if not os.path.isdir(os.path.join(self.path, 'refs')):
            _logger.info("Cloning repository '%s' in '%s'" % (self.name, self.path))
            subprocess.call(['git', 'clone', '--bare', self.name, self.path])

    def _clone_repo_to(self, destination):
        """ Clone from the local repo to destination """
        self.ensure_one()
        if not os.path.exists(destination):
            subprocess.check_output(['git', 'clone', self.path, destination])
        subprocess.check_output(['git', 'pull'], cwd=destination)
        return destination

    def _add_worktree(self, path, treeish):
        if not os.path.exists(os.path.join(path, '.git')):
            _logger.info('Creating worktree %s in "%s" (for %s)', treeish, path, self.short_name)
            try:
                self._git(['worktree', 'add', '-f', path, treeish])
            except subprocess.CalledProcessError as error:
                _logger.warning('Failed to add worktree: %s', error.output.decode())

    def _update_git(self):
        """ Update the git repo on FS """
        self.ensure_one()
        _logger.debug('repo %s updating branches', self.name)

        if not os.path.isdir(self.path):
            os.makedirs(self.path)
        self._clone()
        try:
            self._git(['fetch', '-p', 'origin', '+refs/heads/*:refs/heads/*'])
        except subprocess.CalledProcessError as error:
            _logger.error('git fetch failed with the return code: %s', error.returncode)
            _logger.error('git output: %s', error.output.decode())
