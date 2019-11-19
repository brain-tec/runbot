# -*- coding: utf-8 -*-

import logging
import os
import subprocess

from odoo import models, fields, api


_logger = logging.getLogger(__name__)


class Repo(models.Model):

    _name = "runbot_migra.repo"
    _description = "Github repository"

    name = fields.Char('Repository', required=True)
    path = fields.Char(compute='_get_path', string='Directory', readonly=True)

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
            repo.path = os.path.join(root, 'repo', repo._sanitized_name(repo.name))

    def _git(self, cmd):
        """Execute a git command 'cmd'"""
        self.ensure_one()
        _logger.debug("git command: git (dir %s) %s", self.short_name, ' '.join(cmd))
        cmd = ['git', '--git-dir=%s' % self.path] + cmd
        return subprocess.check_output(cmd).decode('utf-8')

    def _clone(self):
        """ Clone the remote repo if needed """
        self.ensure_one()
        if not os.path.isdir(os.path.join(self.path, 'refs')):
            _logger.info("Cloning repository '%s' in '%s'" % (self.name, self.path))
            subprocess.call(['git', 'clone', '--bare', self.name, self.path])

    def _update_git(self, force):
        """ Update the git repo on FS """
        self.ensure_one()
        _logger.debug('repo %s updating branches', self.name)

        if not os.path.isdir(self.path):
            os.makedirs(self.path)
        self._clone()
        self._git(['fetch', '-p', 'origin', '+refs/heads/*:refs/heads/*'])
