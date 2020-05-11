# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from unittest.mock import patch, DEFAULT


class RunbotCase(TransactionCase):

    def mock_git_helper(self):
        """Helper that returns a mock for repo._git()"""
        def mock_git(repo, cmd):
            if cmd[:2] == ['show', '-s'] or cmd[:3] == ['show', '--pretty="%H -- %s"', '-s']:
                return 'commit message for %s' % cmd[-1]
            if cmd[:2] == ['cat-file', '-e']:
                return True
            if cmd[0] == 'for-each-ref':
                if self.commit_list.get(repo.id):
                    return '\n'.join(['\0'.join(commit_fields) for commit_fields in self.commit_list[repo.id]])
                else:
                    return ''
            else:
                print('Unsupported mock command %s' % cmd)
        return mock_git

    def setUp(self):
        super(RunbotCase, self).setUp()
        self.Project = self.env['runbot.project']
        self.Build = self.env['runbot.build']
        self.BuildParameters = self.env['runbot.build.params']
        self.Repo = self.env['runbot.repo']
        self.Remote = self.env['runbot.remote']
        self.Branch = self.env['runbot.branch']
        self.Version = self.env['runbot.version']
        self.Config = self.env['runbot.build.config']
        self.Commit = self.env['runbot.commit']
        self.BuildCommit = self.env['runbot.build.commit']

        self.project = self.env['runbot.project'].create({'name': 'Tests'})
        self.repo_server = self.env['runbot.repo'].create({
            'name': 'server',
            'project_id': self.project.id,
            'server_files': 'server.py',
        })
        self.repo_addons = self.env['runbot.repo'].create({
            'name': 'addons',
            'project_id': self.project.id,
        })

        self.remote_server = self.env['runbot.remote'].create({
            'name': 'bla@example.com:base/server',
            'repo_id': self.repo_server.id,
            'token': '123',
        })
        self.remote_server_dev = self.env['runbot.remote'].create({
            'name': 'bla@example.com:dev/server',
            'repo_id': self.repo_server.id,
            'token': '123',
        })
        self.remote_addons = self.env['runbot.remote'].create({
            'name': 'bla@example.com:base/addons',
            'repo_id': self.repo_addons.id,
            'token': '123',
        })
        self.remote_addons_dev = self.env['runbot.remote'].create({
            'name': 'bla@example.com:dev/addons',
            'repo_id': self.repo_addons.id,
            'token': '123',
        })

        self.version_master = self.Version.create({'name': '13.0'})
        self.default_config = self.env.ref('runbot.runbot_build_config_default')

        self.base_params = self.BuildParameters.create({
            'version_id': self.version_master.id,
            'project_id': self.project.id,
            'config_id': self.default_config.id,
        })

        self.trigger_server = self.env['runbot.trigger'].create({
            'name': 'Server trigger',
            'repo_ids': [(4, self.repo_server.id)],
            'config_id': self.default_config.id,
            'project_id': self.project.id,
        })

        self.trigger_server = self.env['runbot.trigger'].create({
            'name': 'Addons trigger',
            'repo_ids': [(4, self.repo_addons.id)],
            'dependency_ids': [(4, self.repo_addons.id)],
            'config_id': self.default_config.id,
            'project_id': self.project.id,
        })

        self.patchers = {}
        self.patcher_objects = {}
        self.commit_list = {}


        self.start_patcher('git_patcher', 'odoo.addons.runbot.models.repo.Repo._git', new=self.mock_git_helper())
        self.start_patcher('fqdn_patcher', 'odoo.addons.runbot.common.socket.getfqdn', 'host.runbot.com')
        self.start_patcher('github_patcher', 'odoo.addons.runbot.models.repo.Remote._github', {})
        self.start_patcher('is_on_remote_patcher', 'odoo.addons.runbot.models.branch.Branch._is_on_remote', True)
        self.start_patcher('repo_root_patcher', 'odoo.addons.runbot.models.repo.Repo._root', '/tmp/runbot_test/static')
        self.start_patcher('makedirs', 'odoo.addons.runbot.common.os.makedirs', True)
        self.start_patcher('mkdir', 'odoo.addons.runbot.common.os.mkdir', True)
        self.start_patcher('local_pgadmin_cursor', 'odoo.addons.runbot.common.local_pgadmin_cursor', False)  # avoid to create databases
        self.start_patcher('isdir', 'odoo.addons.runbot.common.os.path.isdir', True)
        self.start_patcher('isfile', 'odoo.addons.runbot.common.os.path.isfile', True)
        self.start_patcher('docker_run', 'odoo.addons.runbot.models.build_config.docker_run')
        self.start_patcher('docker_build', 'odoo.addons.runbot.models.build.docker_build')
        self.start_patcher('docker_ps', 'odoo.addons.runbot.models.repo.docker_ps', [])
        self.start_patcher('docker_stop', 'odoo.addons.runbot.models.repo.docker_stop')
        self.start_patcher('docker_ps', 'odoo.addons.runbot.models.build_config.docker_get_gateway_ip', None)

        self.start_patcher('cr_commit', 'odoo.sql_db.Cursor.commit', None)
        self.start_patcher('repo_commit', 'odoo.addons.runbot.models.repo.Repo._commit', None)
        self.start_patcher('_local_cleanup_patcher', 'odoo.addons.runbot.models.build.BuildResult._local_cleanup')
        self.start_patcher('_local_pg_dropdb_patcher', 'odoo.addons.runbot.models.build.BuildResult._local_pg_dropdb')

    def start_patcher(self, patcher_name, patcher_path, return_value=DEFAULT, side_effect=DEFAULT, new=DEFAULT):

        def stop_patcher_wrapper():
            self.stop_patcher(patcher_name)

        patcher = patch(patcher_path, new=new)
        if not hasattr(patcher, 'is_local'):
            res = patcher.start()
            self.addCleanup(stop_patcher_wrapper)
            self.patchers[patcher_name] = res
            self.patcher_objects[patcher_name] = patcher
            if side_effect != DEFAULT:
                res.side_effect = side_effect
            elif return_value != DEFAULT:
                res.return_value = return_value

    def stop_patcher(self, patcher_name):
        if patcher_name in self.patcher_objects:
            self.patcher_objects[patcher_name].stop()
            del self.patcher_objects[patcher_name]
