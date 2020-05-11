# -*- coding: utf-8 -*-
import datetime
from unittest import skip
from unittest.mock import patch, Mock
from subprocess import CalledProcessError
from odoo.tests import common, TransactionCase
from odoo.tools import mute_logger
import logging
import odoo
import time

from .common import RunbotCase

_logger = logging.getLogger(__name__)


class TestRepo(RunbotCase):

    def setUp(self):
        super(TestRepo, self).setUp()
        self.commit_list = {}
        self.mock_root = self.patchers['repo_root_patcher']

    def test_base_fields(self):
        self.mock_root.return_value = '/tmp/static'

        remote = self.remote_server
        self.assertEqual(remote.path, '/tmp/static/repo/bla_example.com_base_server')
        self.assertEqual(remote.base, 'bla_example.com_base_server')
        self.assertEqual(remote.short_name, 'base/server')

        # HTTPS
        remote.name = 'https://bla@example.com/base/server.git'
        self.assertEqual(remote.short_name, 'base/server')

        # LOCAL
        remote.name = '/path/somewhere/bar.git'
        self.assertEqual(remote.short_name, 'somewhere/bar')


    @patch('odoo.addons.runbot.models.repo.Repo._get_fetch_head_time')
    def test_repo_create_batches(self, mock_fetch_head_time):
        """ Test that when finding new refs in a repo, the missing branches
        are created and new builds are created in pending state
        """

        branch_name = 'master-test'
        def counter():
            i = 100000
            while True:
                i += 1
                yield i


        def github(url, ignore_errors):
            self.assertEqual(ignore_errors, True)
            self.assertEqual(url, '/repos/:owner/:repo/pulls/123')
            return {
                'base' : {'ref': 'master'},
                'head' : {'label': 'dev:%s' % branch_name, 'repo': {'full_name': 'dev/server'}},
            }


        repos = self.repo_addons|self.repo_server

        first_commit = [(
            'refs/%s/heads/%s' % (self.remote_server_dev.remote_name, branch_name),
            'd0d0caca',
            datetime.datetime.now().strftime("%Y-%m-%d, %H:%M:%S"),
            'Marc Bidule',
            '<marc.bidule@somewhere.com>',
            'Server subject',
            'Marc Bidule',
            '<marc.bidule@somewhere.com>')]

        self.commit_list[self.repo_server.id] = first_commit


        mock_fetch_head_time.side_effect = counter()
        self.patchers['github_patcher'].side_effect = github
        repos._create_batches()

        dev_branch = self.env['runbot.branch'].search([('remote_id', '=', self.remote_server_dev.id)])

        bundle = dev_branch.bundle_id
        self.assertEqual(dev_branch.name, branch_name, 'A new branch should have been created')

        batch = self.env['runbot.batch'].search([('bundle_id', '=', bundle.id)])
        self.assertEqual(len(batch), 1, 'Batch found')
        self.assertEqual(batch.batch_commit_ids.commit_id.subject, 'Server subject')
        self.assertEqual(batch.state, 'preparing')
        self.assertEqual(dev_branch.head_name, 'd0d0caca')
        self.assertEqual(bundle.last_batch, batch)
        last_batch = batch

        # create a addons branch in the same bundle
        self.commit_list[self.repo_addons.id] = [('refs/%s/heads/%s' % (self.remote_addons_dev.remote_name, branch_name),
            'deadbeef',
            datetime.datetime.now().strftime("%Y-%m-%d, %H:%M:%S"),
            'Marc Bidule',
            '<marc.bidule@somewhere.com>',
            'Addons subject',
            'Marc Bidule',
            '<marc.bidule@somewhere.com>')]

        repos._create_batches()

        addons_dev_branch = self.env['runbot.branch'].search([('remote_id', '=', self.remote_addons_dev.id)])

        self.assertEqual(addons_dev_branch.bundle_id, bundle)

        self.assertEqual(dev_branch.head_name, 'd0d0caca', "Dev branch head name shoudn't have change")
        self.assertEqual(addons_dev_branch.head_name, 'deadbeef')

        branch_count = self.env['runbot.branch'].search_count([('remote_id', '=', self.remote_server_dev.id)])
        self.assertEqual(branch_count, 1, 'No new branch should have been created')

        batch = self.env['runbot.batch'].search([('bundle_id', '=', bundle.id)])
        self.assertEqual(last_batch, batch, "No new batch should have been created")
        self.assertEqual(bundle.last_batch, batch)
        self.assertEqual(batch.batch_commit_ids.commit_id.mapped('subject'), ['Server subject', 'Addons subject'])

        # create a server pr in the same bundle with the same hash
        self.commit_list[self.repo_server.id] += [
            ('refs/%s/pull/123' % self.remote_server.remote_name,
            'd0d0caca',
            datetime.datetime.now().strftime("%Y-%m-%d, %H:%M:%S"),
            'Marc Bidule',
            '<marc.bidule@somewhere.com>',
            'Another subject',
            'Marc Bidule',
            '<marc.bidule@somewhere.com>')]

        # Create Batches
        repos._create_batches()

        pull_request = self.env['runbot.branch'].search([('remote_id', '=', self.remote_server.id)])
        self.assertEqual(pull_request.bundle_id, bundle)

        self.assertEqual(dev_branch.head_name, 'd0d0caca')
        self.assertEqual(pull_request.head_name, 'd0d0caca')
        self.assertEqual(addons_dev_branch.head_name, 'deadbeef')

        self.assertEqual(dev_branch, self.env['runbot.branch'].search([('remote_id', '=', self.remote_server_dev.id)]))
        self.assertEqual(pull_request, self.env['runbot.branch'].search([('remote_id', '=', self.remote_server.id)]))
        self.assertEqual(addons_dev_branch, self.env['runbot.branch'].search([('remote_id', '=', self.remote_addons_dev.id)]))

        batch = self.env['runbot.batch'].search([('bundle_id', '=', bundle.id)])
        self.assertEqual(last_batch, batch, "No new batch should have been created")
        self.assertEqual(bundle.last_batch, batch)
        self.assertEqual(batch.batch_commit_ids.commit_id.mapped('subject'), ['Server subject', 'Addons subject'])

        # A new commit is found in the server repo
        self.commit_list[self.repo_server.id] = [
            (
                'refs/%s/heads/%s' % (self.remote_server_dev.remote_name, branch_name),
                'b00b',
                datetime.datetime.now().strftime("%Y-%m-%d, %H:%M:%S"),
                'Marc Bidule',
                '<marc.bidule@somewhere.com>',
                'A new subject',
                'Marc Bidule',
                '<marc.bidule@somewhere.com>'
            ),
            (
                'refs/%s/pull/123' % self.remote_server.remote_name,
                'b00b',
                datetime.datetime.now().strftime("%Y-%m-%d, %H:%M:%S"),
                'Marc Bidule',
                '<marc.bidule@somewhere.com>',
                'A new subject',
                'Marc Bidule',
                '<marc.bidule@somewhere.com>'
            )]

        # Create Batches
        repos._create_batches()

        self.assertEqual(dev_branch, self.env['runbot.branch'].search([('remote_id', '=', self.remote_server_dev.id)]))
        self.assertEqual(pull_request, self.env['runbot.branch'].search([('remote_id', '=', self.remote_server.id)]))
        self.assertEqual(addons_dev_branch, self.env['runbot.branch'].search([('remote_id', '=', self.remote_addons_dev.id)]))

        batch = self.env['runbot.batch'].search([('bundle_id', '=', bundle.id)])
        self.assertEqual(bundle.last_batch, batch)
        self.assertEqual(len(batch), 1, 'No new batch created, updated')
        self.assertEqual(batch.batch_commit_ids.commit_id.mapped('subject'),  ['A new subject', 'Addons subject'], 'commits should have been updated')
        self.assertEqual(batch.state, 'preparing')

        self.assertEqual(dev_branch.head_name, 'b00b')
        self.assertEqual(pull_request.head_name, 'b00b')
        self.assertEqual(addons_dev_branch.head_name, 'deadbeef')

        # TODO move this
        # previous_build = self.env['runbot.build'].search([('repo_id', '=', repo.id), ('branch_id', '=', branch.id), ('name', '=', 'd0d0caca')])
        # self.assertEqual(previous_build.local_state, 'done', 'Previous pending build should be done')
        # self.assertEqual(previous_build.local_result, 'skipped', 'Previous pending build result should be skipped')

        batch.state = 'done'

        repos._create_batches()

        batch = self.env['runbot.batch'].search([('bundle_id', '=', bundle.id)])
        self.assertEqual(len(batch), 1, 'No new batch created, no head change')

        self.commit_list[self.repo_server.id] = [
            ('refs/%s/heads/%s' % (self.remote_server_dev.remote_name, branch_name),
            'dead1234',
            datetime.datetime.now().strftime("%Y-%m-%d, %H:%M:%S"),
            'Marc Bidule',
            '<marc.bidule@somewhere.com>',
            'A last subject',
            'Marc Bidule',
            '<marc.bidule@somewhere.com>')]

        repos._create_batches()

        bundles = self.env['runbot.bundle'].search([])
        self.assertEqual(bundles, bundle)
        batches = self.env['runbot.batch'].search([('bundle_id', '=', bundle.id)])
        self.assertEqual(len(batches), 2, 'No preparing instance and new head -> new batch')
        self.assertEqual(bundle.last_batch.state, 'preparing')
        self.assertEqual(bundle.last_batch.batch_commit_ids.commit_id.subject, 'A last subject')

        self.commit_list[self.repo_server.id] = first_commit  # branch reset hard to an old commit (and pr closed)

        repos._create_batches()

        batch = self.env['runbot.batch'].search([('bundle_id', '=', bundle.id)], order='id desc')
        self.assertEqual(len(batch), 2, 'No new batch created, updated')
        self.assertEqual(bundle.last_batch.batch_commit_ids.commit_id.mapped('subject'), ['Server subject'], 'commits should have been updated')
        self.assertEqual(bundle.last_batch.state, 'preparing')
        self.assertEqual(dev_branch.head_name, 'd0d0caca')

        bundle.last_batch._start()
        self.assertEqual(bundle.last_batch.batch_commit_ids.commit_id.mapped('subject'), ['Server subject', 'Addons subject'])


        # TODO imp
        # Add another branch in another project
        # Add another bundle


    @skip('This test is for performances. It needs a lot of real branches in DB to mean something')
    def test_repo_perf_find_new_commits(self):
        mock_root.return_value = '/tmp/static'
        repo = self.env['runbot.repo'].search([('name', '=', 'blabla')])

        self.commit_list[self.repo_server.id] = []

        # create 20000 branches and refs
        start_time = time.time()
        self.env['runbot.build'].search([], limit=5).write({'name': 'jflsdjflj'})

        for i in range(20005):
            self.commit_list[self.repo_server.id].append(['refs/heads/bidon-%05d' % i,
                                     'd0d0caca %s' % i,
                                     datetime.datetime.now().strftime("%Y-%m-%d, %H:%M:%S"),
                                     'Marc Bidule',
                                     '<marc.bidule@somewhere.com>',
                                     'A nice subject',
                                     'Marc Bidule',
                                     '<marc.bidule@somewhere.com>'])
        inserted_time = time.time()
        _logger.info('Insert took: %ssec', (inserted_time - start_time))
        repo._create_batches()

        _logger.info('Create pending builds took: %ssec', (time.time() - inserted_time))

    @common.warmup
    def test_times(self):
        def _test_times(model, setter, field_name):
            repo1 = self.Repo.create({'name': 'bla@example.com:foo/bar', 'repo_group_id': self.repo_group.id})
            repo2 = self.Repo.create({'name': 'bla@example.com:foo2/bar', 'repo_group_id': self.repo_group.id})
            count = self.cr.sql_log_count
            with self.assertQueryCount(1):
                getattr(repo1, setter)(1.1)
            getattr(repo2, setter)(1.2)
            self.assertEqual(len(self.env[model].search([])), 2)
            self.assertEqual(repo1[field_name], 1.1)
            self.assertEqual(repo2[field_name], 1.2)

            getattr(repo1, setter)(1.3)
            getattr(repo2, setter)(1.4)

            self.assertEqual(len(self.env[model].search([])), 4)
            self.assertEqual(repo1[field_name], 1.3)
            self.assertEqual(repo2[field_name], 1.4)

            self.Repo.invalidate_cache()
            self.assertEqual(repo1[field_name], 1.3)
            self.assertEqual(repo2[field_name], 1.4)

            self.Repo._gc_times()

            self.assertEqual(len(self.env[model].search([])), 2)
            self.assertEqual(repo1[field_name], 1.3)
            self.assertEqual(repo2[field_name], 1.4)

        _test_times('runbot.repo.hooktime', 'set_hook_time', 'hook_time')
        _test_times('runbot.repo.reftime', 'set_ref_time', 'get_ref_time')


class TestGithub(TransactionCase):
    def test_github(self):
        """ Test different github responses or failures"""
        self.project = self.env['runbot.project'].create({'name': 'Tests'})
        self.repo_group = self.env['runbot.repo.group'].create({
            'name': 'bar', 
            'project_id': self.project.id,
            'server_files': 'server.py'
        })
        repo = self.env['runbot.repo'].create({'name': 'bla@example.com:foo/bar', 'repo_group_id': self.repo_group.id})
        self.assertEqual(repo._github('/repos/:owner/:repo/statuses/abcdef', dict(), ignore_errors=True), None, 'A repo without token should return None')
        repo.token = 'abc'
        with patch('odoo.addons.runbot.models.repo.requests.Session') as mock_session:
            with self.assertRaises(Exception, msg='should raise an exception with ignore_errors=False'):
                mock_session.return_value.post.side_effect = Exception('301: Bad gateway')
                repo._github('/repos/:owner/:repo/statuses/abcdef', {'foo': 'bar'}, ignore_errors=False)

            mock_session.return_value.post.reset_mock()
            with self.assertLogs(logger='odoo.addons.runbot.models.repo') as assert_log:
                repo._github('/repos/:owner/:repo/statuses/abcdef', {'foo': 'bar'}, ignore_errors=True)
                self.assertIn('Ignored github error', assert_log.output[0])

            self.assertEqual(2, mock_session.return_value.post.call_count, "_github method should try two times by default")

            mock_session.return_value.post.reset_mock()
            mock_session.return_value.post.side_effect = [Exception('301: Bad gateway'), Mock()]
            with self.assertLogs(logger='odoo.addons.runbot.models.repo') as assert_log:
                repo._github('/repos/:owner/:repo/statuses/abcdef', {'foo': 'bar'}, ignore_errors=True)
                self.assertIn('Success after 2 tries', assert_log.output[0])

            self.assertEqual(2, mock_session.return_value.post.call_count, "_github method should try two times by default")


class TestFetch(RunbotCase):

    def setUp(self):
        super(TestFetch, self).setUp()
        self.mock_root = self.patchers['repo_root_patcher']

    def test_update_fetch_cmd(self):
        """ Test that git fetch is tried multiple times before disabling host """

        fetch_count = 0
        force_failure = False

        def git_side_effect(cmd):
            nonlocal fetch_count
            fetch_count += 1
            if fetch_count < 3 or force_failure:
                raise CalledProcessError(128, cmd, 'Dummy Error'.encode('utf-8'))
            else:
                return True

        git_patcher = self.patchers['git_patcher']
        git_patcher.side_effect = git_side_effect

        repo = self.Repo.create({'name': 'bla@example.com:foo/bar', 'repo_group_id': self.repo_group.id})
        host = self.env['runbot.host']._get_current()

        self.assertFalse(host.assigned_only)
        # Ensure that Host is not disabled if fetch succeeds after 3 tries
        with mute_logger("odoo.addons.runbot.models.repo"):
            repo._update_fetch_cmd()
        self.assertFalse(host.assigned_only, "Host should not be disabled when fetch succeeds")
        self.assertEqual(fetch_count, 3)

        # Now ensure that host is disabled after 5 unsuccesful tries
        force_failure = True
        fetch_count = 0
        with mute_logger("odoo.addons.runbot.models.repo"):
            repo._update_fetch_cmd()
        self.assertTrue(host.assigned_only)
        self.assertEqual(fetch_count, 5)


class TestRepoScheduler(RunbotCase):

    def setUp(self):
        # as the _scheduler method commits, we need to protect the database
        registry = odoo.registry()
        super(TestRepoScheduler, self).setUp()

        self.fqdn_patcher = patch('odoo.addons.runbot.models.host.fqdn')
        mock_root = self.patchers['repo_root_patcher']
        mock_root.return_value = '/tmp/static'

        self.foo_repo = self.Repo.create({'name': 'bla@example.com:foo/bar', 'repo_group_id': self.repo_group.id})

        self.foo_branch = self.Branch.create({
            'repo_id': self.foo_repo.id,
            'name': 'refs/head/foo'
        })

    @patch('odoo.addons.runbot.models.build.BuildResult._kill')
    @patch('odoo.addons.runbot.models.build.BuildResult._schedule')
    @patch('odoo.addons.runbot.models.build.BuildResult._init_pendings')
    def test_repo_scheduler(self, mock_init_pendings, mock_schedule, mock_kill):

        self.env['ir.config_parameter'].set_param('runbot.runbot_workers', 6)
        builds = []
        # create 6 builds that are testing on the host to verify that
        # workers are not overfilled
        for build_name in ['a', 'b', 'c', 'd', 'e', 'f']:
            build = self.Build.create({
                'branch_id': self.foo_branch.id,
                'name': build_name,
                'port': '1234',
                'build_type': 'normal',
                'local_state': 'testing',
                'host': 'host.runbot.com'
            })
            builds.append(build)
        # now the pending build that should stay unasigned
        scheduled_build = self.Build.create({
            'branch_id': self.foo_branch.id,
            'name': 'sched_build',
            'port': '1234',
            'build_type': 'scheduled',
            'local_state': 'pending',
        })
        builds.append(scheduled_build)
        # create the build that should be assigned once a slot is available
        build = self.Build.create({
            'branch_id': self.foo_branch.id,
            'name': 'foobuild',
            'port': '1234',
            'build_type': 'normal',
            'local_state': 'pending',
        })
        builds.append(build)
        host = self.env['runbot.host']._get_current()
        self.foo_repo._scheduler(host)

        build.invalidate_cache()
        scheduled_build.invalidate_cache()
        self.assertFalse(build.host)
        self.assertFalse(scheduled_build.host)

        # give some room for the pending build
        self.Build.search([('name', '=', 'a')]).write({'local_state': 'done'})

        self.foo_repo._scheduler(host)
