# -*- coding: utf-8 -*-
from collections import defaultdict
from odoo.tests.common import TransactionCase
from unittest.mock import patch

class RunbotCase(TransactionCase):

    def setUp(self):
        super(RunbotCase, self).setUp()

        self.Build = self.env['runbot.build']
        self.Repo = self.env['runbot.repo']
        self.Branch = self.env['runbot.branch']

        self.get_params_patcher = patch('odoo.addons.runbot.models.build.runbot_build._get_params')
        self.git_patcher = patch('odoo.addons.runbot.models.repo.runbot_repo._git')
        self.fqdn_patcher = patch('odoo.addons.runbot.models.build.fqdn')
        self.grep_patcher = patch('odoo.addons.runbot.models.build.grep')
        self.github_patcher = patch('odoo.addons.runbot.models.repo.runbot_repo._github')
        self.is_on_remote_patcher = patch('odoo.addons.runbot.models.branch.runbot_branch._is_on_remote')
        self.repo_root_patcher = patch('odoo.addons.runbot.models.repo.runbot_repo._root')

    def start_patcher(self, patcher_name):
        patcher = getattr(self, patcher_name)
        if not hasattr(patcher, 'is_local'):
            res = patcher.start()
            self.addCleanup(patcher.stop)
            return res
        return patcher

    def start_get_params_patcher(self):
        mock_get_params = self.start_patcher('get_params_patcher')
        mock_get_params.return_value = defaultdict(lambda: defaultdict(str))
        return mock_get_params

    def create_build(self, vals):
        self.start_get_params_patcher()
        return self.Build.create(vals)
