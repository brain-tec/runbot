# -*- coding: utf-8 -*-
from collections import defaultdict
from odoo.tests.common import TransactionCase
from unittest.mock import patch
from subprocess import CalledProcessError
import re

class Dummy():
    ...


class RunbotCase(TransactionCase):

    def setUp(self):
        super(RunbotCase, self).setUp()

        self.Build = self.env['runbot.build']
        self.Repo = self.env['runbot.repo']
        self.Branch = self.env['runbot.branch']

        self.patchers = {}

        def git_side_effect(cmd):
            if re.match(r'show (--pretty="%H -- %s" )?-s (.*)', ' '.join(cmd)):
                return 'commit message for %s' % cmd[-1]
            else:
                print('Unsupported mock command %s' % cmd)

        self.start_patcher('git_patcher', 'odoo.addons.runbot.models.repo.runbot_repo._git', side_effect=git_side_effect)
        self.start_patcher('fqdn_patcher', 'odoo.addons.runbot.common.socket.getfqdn', 'host.runbot.com')
        self.start_patcher('grep_patcher', 'odoo.addons.runbot.models.build.grep', True)
        self.start_patcher('github_patcher', 'odoo.addons.runbot.models.repo.runbot_repo._github', {})
        self.start_patcher('is_on_remote_patcher', 'odoo.addons.runbot.models.branch.runbot_branch._is_on_remote', True)
        self.start_patcher('repo_root_patcher', 'odoo.addons.runbot.models.repo.runbot_repo._root', '/tmp/runbot_test/static')
        self.start_patcher('makedirs', 'odoo.addons.runbot.common.os.makedirs', True)
        self.start_patcher('mkdir', 'odoo.addons.runbot.common.os.mkdir', True)


    def start_patcher(self, patcher_name, patcher_path, return_value=Dummy, side_effect=Dummy):
        patcher = patch(patcher_path)
        if not hasattr(patcher, 'is_local'):
            res = patcher.start()
            self.addCleanup(patcher.stop)
            self.patchers[patcher_name] = res
            if side_effect != Dummy:
                res.side_effect = side_effect
            elif return_value != Dummy:
                res.return_value = return_value
            else:
                def _side_effect(*args, **kwargs):
                    print(patcher_name, 'calledwith', args, kwargs)
                res.side_effect = _side_effect

    def create_build(self, vals):
        return self.Build.create(vals)
