# -*- coding: utf-8 -*-
import datetime

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from .common import RunbotCase, RunbotCaseMinimalSetup


def rev_parse(repo, branch_name):
    """
    simulate a rev parse by returning a fake hash of form
    'rp_odoo-dev/enterprise_saas-12.2__head'
    should be overwitten if a pr head should match a branch head
    """
    head_hash = 'rp_%s_%s_head' % (repo.name.split(':')[1], branch_name.split('/')[-1])
    return head_hash


class TestBuildParams(RunbotCaseMinimalSetup):

    def setUp(self):
        super(TestBuildParams, self).setUp()

    def test_params(self):

        server_commit = self.Commit.create({
            'name': 'dfdfcfcf0000ffffffffffffffffffffffffffff',
            'repo_id': self.repo_server.id
        })

        params = self.BuildParameters.create({
            'version_id': self.version_13.id,
            'project_id': self.project.id,
            'config_id': self.default_config.id,
            'commit_link_ids': [
                (0, 0, {'commit_id': server_commit.id})
            ],
            'config_data': {'foo': 'bar'}
        })

        # test that when the same params does not create a new record
        same_params = self.BuildParameters.create({
            'version_id': self.version_13.id,
            'project_id': self.project.id,
            'config_id': self.default_config.id,
            'commit_link_ids': [
                (0, 0, {'commit_id': server_commit.id})
            ],
            'config_data': {'foo': 'bar'}
        })

        self.assertEqual(params.fingerprint, same_params.fingerprint)
        self.assertEqual(params.id, same_params.id)

        # test that params cannot be overwitten
        with self.assertRaises(UserError):
            params.write({'modules': 'bar'})

        # Test that a copied param without changes does not create a new record
        copied_params = params.copy()
        self.assertEqual(copied_params.id, params.id)

        # Test copy with a parameter change
        other_commit = self.Commit.create({
            'name': 'deadbeef0000ffffffffffffffffffffffffffff',
            'repo_id': self.repo_server.id
        })

        copied_params = params.copy({
            'commit_link_ids': [
                (0, 0, {'commit_id': other_commit.id})
            ]
        })
        self.assertNotEqual(copied_params.id, params.id)

    def test_trigger_build_config(self):
        """Test that a build gets the build config from the trigger"""
        self.additionnal_setup()
        self.start_patchers()

        self.trigger_server.description = expected_description = "A nice trigger description"

        # A commit is found on the dev remote
        branch_a_name = '10.0-test-something'
        self.push_commit(self.remote_server_dev, branch_a_name, 'nice subject', sha='d0d0caca')

        # batch preparation
        self.repo_server._update_batches()

        # prepare last_batch
        bundle = self.env['runbot.bundle'].search([('name', '=', branch_a_name), ('project_id', '=', self.project.id)])
        bundle.last_batch._prepare()
        build_slot = bundle.last_batch.slot_ids.filtered(lambda rec: rec.trigger_id == self.trigger_server)
        self.assertEqual(build_slot.build_id.params_id.config_id, self.trigger_server.config_id)
        self.assertEqual(build_slot.build_id.description, expected_description, "A build description should reflect the trigger description")

    def test_custom_trigger_config(self):
        """Test that a bundle with a custom trigger creates a build with appropriate config"""
        self.additionnal_setup()
        self.start_patchers()

        # A commit is found on the dev remote
        branch_a_name = '10.0-test-something'
        self.push_commit(self.remote_server_dev, branch_a_name, 'nice subject', sha='d0d0caca')
        # batch preparation
        self.repo_server._update_batches()

        # create a custom config and a new trigger
        custom_config = self.env['runbot.build.config'].create({'name': 'A Custom Config'})

        # create a custom trigger for the bundle
        bundle = self.Bundle.search([('name', '=', branch_a_name), ('project_id', '=', self.project.id)])

        # create a custom trigger with the custom config linked to the bundle
        self.env['runbot.trigger.custom'].create({
            'trigger_id': self.trigger_server.id,
            'bundle_id': bundle.id,
            'config_id': custom_config.id
        })

        bundle.last_batch._prepare()
        build_slot = bundle.last_batch.slot_ids.filtered(lambda rec: rec.trigger_id == self.trigger_server)
        self.assertEqual(build_slot.build_id.params_id.config_id, custom_config)


class TestBuildResult(RunbotCase):

    def setUp(self):
        super(TestBuildResult, self).setUp()

        self.server_commit = self.Commit.create({
            'name': 'dfdfcfcf0000ffffffffffffffffffffffffffff',
            'repo_id': self.repo_server.id
        })

        self.addons_commit = self.Commit.create({
            'name': 'd0d0caca0000ffffffffffffffffffffffffffff',
            'repo_id': self.repo_addons.id,
        })

        self.server_params = self.base_params.copy({'commit_link_ids': [
            (0, 0, {'commit_id': self.server_commit.id})
        ]})

        self.addons_params = self.base_params.copy({'commit_link_ids': [
            (0, 0, {'commit_id': self.server_commit.id}),
            (0, 0, {'commit_id': self.addons_commit.id})
        ]})

        self.start_patcher('find_patcher', 'odoo.addons.runbot.common.find', 0)

    def test_base_fields(self):

        build = self.Build.create({
            'params_id': self.server_params.id,
            'port': '1234'
        })

        self.assertEqual(build.dest, '%05d-13-0' % build.id)

        # Test domain compute with fqdn and ir.config_parameter
        self.env['ir.config_parameter'].sudo().set_param('runbot.runbot_nginx', False)
        self.patchers['fqdn_patcher'].return_value = 'runbot98.nowhere.org'
        self.env['ir.config_parameter'].sudo().set_param('runbot.runbot_domain', False)
        self.assertEqual(build.domain, 'runbot98.nowhere.org:1234')
        self.env['ir.config_parameter'].set_param('runbot.runbot_domain', 'runbot99.example.org')
        build._compute_domain()
        self.assertEqual(build.domain, 'runbot99.example.org:1234')

        # test json stored _data field and data property
        #self.assertEqual(build.params_id.config_data, {})
        #build.params_id.config_data = {'restore_url': 'foobar'}
        #self.assertEqual(build.params_id.config_data, {'restore_url': 'foobar'})
        #build.params_id.config_data['test_info'] = 'dummy'
        #self.assertEqual(build.params_id.config_data, {"restore_url": "foobar", "test_info": "dummy"})
        #del build.params_id.config_data['restore_url']
        #self.assertEqual(build.params_id.config_data, {"test_info": "dummy"})

        other = self.Build.create({
            'params_id': self.server_params.id,
            'local_result': 'ko'
        })

        # test a bulk write, that one cannot change from 'ko' to 'ok'
        builds = self.Build.browse([build.id, other.id])
        with self.assertRaises(AssertionError):
            builds.write({'local_result': 'ok'})

    def test_markdown_description(self):
        build = self.Build.create({
            'params_id': self.server_params.id,
            'description': 'A nice **description**'
        })
        self.assertEqual(build.md_description, 'A nice <strong>description</strong>')

        build.description = "<script>console.log('foo')</script>"
        self.assertEqual(build.md_description, "&lt;script&gt;console.log('foo')&lt;/script&gt;")

    @patch('odoo.addons.runbot.models.build.BuildResult._get_available_modules')
    def test_filter_modules(self, mock_get_available_modules):
        """ test module filtering """

        build = self.Build.create({
            'params_id': self.addons_params.id,
        })

        mock_get_available_modules.return_value = {
            self.repo_server: ['good_module', 'bad_module', 'other_good', 'l10n_be', 'hw_foo', 'hwgood', 'hw_explicit'],
            self.repo_addons: ['other_mod_1', 'other_mod_2'],
        }

        self.repo_server.modules = '-bad_module,-hw_*,hw_explicit,-l10n_*'
        self.repo_addons.modules = '-*'

        modules_to_test = build._get_modules_to_test(modules_patterns='')
        self.assertEqual(modules_to_test, sorted(['good_module', 'hwgood', 'other_good', 'hw_explicit']))

        modules_to_test = build._get_modules_to_test(modules_patterns='-*, l10n_be')
        self.assertEqual(modules_to_test, sorted(['l10n_be']))
        modules_to_test = build._get_modules_to_test(modules_patterns='l10n_be')
        self.assertEqual(modules_to_test, sorted(['good_module', 'hwgood', 'other_good', 'hw_explicit', 'l10n_be']))
        # star to get all available mods
        modules_to_test = build._get_modules_to_test(modules_patterns='*, -hw_*, hw_explicit')
        self.assertEqual(modules_to_test, sorted(['good_module', 'bad_module', 'other_good', 'l10n_be', 'hwgood', 'hw_explicit', 'other_mod_1', 'other_mod_2']))

    def test_build_cmd_log_db(self, ):
        """ test that the logdb connection URI is taken from the .odoorc file """
        uri = 'postgres://someone:pass@somewhere.com/db'
        self.env['ir.config_parameter'].sudo().set_param("runbot.runbot_logdb_uri", uri)

        build = self.Build.create({
            'params_id': self.server_params.id,
        })
        cmd = build._cmd(py_version=3)
        self.assertIn('log_db = %s' % uri, cmd.get_config())

    def test_build_cmd_server_path_no_dep(self):
        """ test that the server path and addons path """
        build = self.Build.create({
            'params_id': self.server_params.id,
        })
        cmd = build._cmd(py_version=3)
        self.assertEqual('python3', cmd[0])
        self.assertEqual('server/server.py', cmd[1])
        self.assertIn('--addons-path', cmd)
        # TODO fix the _get_addons_path and/or _docker_source_folder
        # addons_path_pos = cmd.index('--addons-path') + 1
        # self.assertEqual(cmd[addons_path_pos], 'bar/addons,bar/core/addons')

    def test_build_cmd_server_path_with_dep(self):
        """ test that the server path and addons path are correct"""

        def is_file(file):
            self.assertIn(file, [
                '/tmp/runbot_test/static/sources/addons/d0d0caca0000ffffffffffffffffffffffffffff/requirements.txt',
                '/tmp/runbot_test/static/sources/server/dfdfcfcf0000ffffffffffffffffffffffffffff/requirements.txt',
                '/tmp/runbot_test/static/sources/server/dfdfcfcf0000ffffffffffffffffffffffffffff/server.py',
                '/tmp/runbot_test/static/sources/server/dfdfcfcf0000ffffffffffffffffffffffffffff/openerp/tools/config.py'
            ])
            if file == '/tmp/runbot_test/static/sources/addons/d0d0caca0000ffffffffffffffffffffffffffff/requirements.txt':
                return False
            return True

        def is_dir(file):
            paths = [
                'sources/server/dfdfcfcf0000ffffffffffffffffffffffffffff/addons',
                'sources/server/dfdfcfcf0000ffffffffffffffffffffffffffff/core/addons',
                'sources/addons/d0d0caca0000ffffffffffffffffffffffffffff'
            ]
            self.assertTrue(any([path in file for path in paths]))  # checking that addons path existence check looks ok
            return True

        self.patchers['isfile'].side_effect = is_file
        self.patchers['isdir'].side_effect = is_dir

        build = self.Build.create({
            'params_id': self.addons_params.id,
        })

        cmd = build._cmd(py_version=3)
        self.assertIn('--addons-path', cmd)
        addons_path_pos = cmd.index('--addons-path') + 1
        self.assertEqual(cmd[addons_path_pos], 'server/addons,server/core/addons,addons')
        self.assertEqual('server/server.py', cmd[1])
        self.assertEqual('python3', cmd[0])

    def test_build_gc_date(self):
        """ test build gc date and gc_delay"""
        build = self.Build.create({
            'params_id': self.server_params.id,
            'local_state': 'done'
        })

        child_build = self.Build.create({
            'params_id': self.server_params.id,
            'parent_id': build.id,
            'local_state': 'done'
        })

        # verify that the gc_day is set 30 days later (29 days since we should be a few microseconds later)
        delta = fields.Datetime.from_string(build.gc_date) - datetime.datetime.now()
        self.assertEqual(delta.days, 29)
        child_delta = fields.Datetime.from_string(child_build.gc_date) - datetime.datetime.now()
        self.assertEqual(child_delta.days, 14)

        # Keep child build ten days more
        child_build.gc_delay = 10
        child_delta = fields.Datetime.from_string(child_build.gc_date) - datetime.datetime.now()
        self.assertEqual(child_delta.days, 24)

        # test the real _local_cleanup method
        self.stop_patcher('_local_cleanup_patcher')
        self.start_patcher('build_local_pgadmin_cursor_patcher', 'odoo.addons.runbot.models.build.local_pgadmin_cursor')
        self.start_patcher('build_os_listdirr_patcher', 'odoo.addons.runbot.models.build.os.listdir')
        dbname = '%s-foobar' % build.dest
        self.start_patcher('list_local_dbs_patcher', 'odoo.addons.runbot.models.build.list_local_dbs', return_value=[dbname])

        build._local_cleanup()
        self.assertFalse(self.patchers['_local_pg_dropdb_patcher'].called)
        build.job_end = datetime.datetime.now() - datetime.timedelta(days=31)
        build._local_cleanup()
        self.patchers['_local_pg_dropdb_patcher'].assert_called_with(dbname)

    @patch('odoo.addons.runbot.models.build._logger')
    def test_build_skip(self, mock_logger):
        """test build is skipped"""
        build = self.Build.create({
            'params_id': self.server_params.id,
            'port': '1234',
        })
        build._skip()
        self.assertEqual(build.local_state, 'done')
        self.assertEqual(build.local_result, 'skipped')

        other_build = self.Build.create({
            'params_id': self.server_params.id,
            'port': '1234',
        })
        other_build._skip(reason='A good reason')
        self.assertEqual(other_build.local_state, 'done')
        self.assertEqual(other_build.local_result, 'skipped')
        log_first_part = '%s skip %%s' % (other_build.dest)
        mock_logger.debug.assert_called_with(log_first_part, 'A good reason')

    #def test_ask_kill_duplicate(self):
    #    """ Test that the _ask_kill method works on duplicate when they are related PR/branch"""
#
    #    branch = self.Branch.create({
    #        'repo_id': self.repo.id,
    #        'name': 'refs/heads/master-test-branch-xxx'
    #    })
#
    #    pr = self.Branch.create({
    #        'repo_id': self.repo.id,
    #        'name': 'refs/pull/1234',
    #        'pull_head_name': 'odoo:master-test-branch-xxx'
    #    })
#
    #    build1 = self.Build.create({
    #        'branch_id': branch.id,
    #        'name': 'd0d0caca0000ffffffffffffffffffffffffffff',
    #    })
    #    build2 = self.Build.create({
    #        'branch_id': pr.id,
    #        'name': 'd0d0caca0000ffffffffffffffffffffffffffff',
    #    })
    #    build2.write({'local_state': 'duplicate', 'duplicate_id': build1.id}) # this may not be usefull if we detect duplicate in same repo.
#
    #    self.assertEqual(build1.local_state, 'pending')
    #    build2._ask_kill()
    #    self.assertEqual(build1.local_state, 'done', 'A killed pending duplicate build should mark the real build as done')
    #    self.assertEqual(build1.local_result, 'skipped', 'A killed pending duplicate build should mark the real build as skipped')

    def test_children(self):
        build1 = self.Build.create({
            'params_id': self.server_params.id,
        })
        build1_1 = self.Build.create({
            'params_id': self.server_params.id,
            'parent_id': build1.id,
        })
        build1_2 = self.Build.create({
            'params_id': self.server_params.id,
            'parent_id': build1.id,
        })
        build1_1_1 = self.Build.create({
            'params_id': self.server_params.id,
            'parent_id': build1_1.id,
        })
        build1_1_2 = self.Build.create({
            'params_id': self.server_params.id,
            'parent_id': build1_1.id,
        })

        def assert_state(global_state, build):
            self.assertEqual(build.global_state, global_state)

        assert_state('pending', build1)
        assert_state('pending', build1_1)
        assert_state('pending', build1_2)
        assert_state('pending', build1_1_1)
        assert_state('pending', build1_1_2)

        build1.local_state = 'testing'
        build1_1.local_state = 'testing'
        build1.local_state = 'done'
        build1_1.local_state = 'done'

        assert_state('waiting', build1)
        assert_state('waiting', build1_1)
        assert_state('pending', build1_2)
        assert_state('pending', build1_1_1)
        assert_state('pending', build1_1_2)

        build1_1_1.local_state = 'testing'

        assert_state('waiting', build1)
        assert_state('waiting', build1_1)
        assert_state('pending', build1_2)
        assert_state('testing', build1_1_1)
        assert_state('pending', build1_1_2)

        build1_2.local_state = 'testing'

        assert_state('waiting', build1)
        assert_state('waiting', build1_1)
        assert_state('testing', build1_2)
        assert_state('testing', build1_1_1)
        assert_state('pending', build1_1_2)

        build1_2.local_state = 'testing'  # writing same state a second time

        assert_state('waiting', build1)
        assert_state('waiting', build1_1)
        assert_state('testing', build1_2)
        assert_state('testing', build1_1_1)
        assert_state('pending', build1_1_2)

        build1_1_2.local_state = 'done'
        build1_1_1.local_state = 'done'
        build1_2.local_state = 'done'

        assert_state('done', build1)
        assert_state('done', build1_1)
        assert_state('done', build1_2)
        assert_state('done', build1_1_1)
        assert_state('done', build1_1_2)


class TestGc(RunbotCaseMinimalSetup):

    def test_repo_gc_testing(self):
        """ test that builds are killed when room is needed on a host """

        self.additionnal_setup()

        self.start_patchers()

        host = self.env['runbot.host'].create({
            'name': 'runbot_xxx',
            'nb_worker': 2
        })

        # A commit is found on the dev remote
        branch_a_name = '10.0-test-something'
        self.push_commit(self.remote_server_dev, branch_a_name, 'nice subject', sha='d0d0caca')

        # batch preparation
        self.repo_server._update_batches()

        # prepare last_batch
        bundle_a = self.env['runbot.bundle'].search([('name', '=', branch_a_name)])
        bundle_a.last_batch._prepare()

        # now we should have a build in pending state in the bundle
        self.assertEqual(len(bundle_a.last_batch.slot_ids), 2)
        build_a = bundle_a.last_batch.slot_ids[0].build_id
        self.assertEqual(build_a.global_state, 'pending')

        # now another commit is found in another branch
        branch_b_name = '11.0-test-other-thing'
        self.push_commit(self.remote_server_dev, branch_b_name, 'other subject', sha='cacad0d0')
        self.repo_server._update_batches()
        bundle_b = self.env['runbot.bundle'].search([('name', '=', branch_b_name)])
        bundle_b.last_batch._prepare()

        build_b = bundle_b.last_batch.slot_ids[0].build_id

        # the two builds are starting tests on two different hosts
        build_a.write({'local_state': 'testing', 'host': host.name})
        build_b.write({'local_state': 'testing', 'host': 'runbot_yyy'})

        # no room needed, verify that nobody got killed
        self.Runbot._gc_testing(host)
        self.assertFalse(build_a.requested_action)
        self.assertFalse(build_b.requested_action)

        # a new commit is pushed on branch_a
        self.push_commit(self.remote_server_dev, branch_a_name, 'new subject', sha='d0cad0ca')
        self.repo_server._update_batches()
        bundle_a = self.env['runbot.bundle'].search([('name', '=', branch_a_name)])
        bundle_a.last_batch._prepare()
        build_a_last = bundle_a.last_batch.slot_ids[0].build_id
        self.assertEqual(build_a_last.local_state, 'pending')
        self.assertTrue(build_a.killable, 'The previous build in the batch should be killable')

        # the build_b create a child build
        children_b = self.Build.create({
            'params_id': build_b.params_id.copy().id,
            'parent_id': build_b.id,
            'build_type': build_b.build_type,
        })

        # no room needed, verify that nobody got killed
        self.Runbot._gc_testing(host)
        self.assertFalse(build_a.requested_action)
        self.assertFalse(build_b.requested_action)
        self.assertFalse(build_a_last.requested_action)
        self.assertFalse(children_b.requested_action)

        # now children_b starts on runbot_xxx
        children_b.write({'local_state': 'testing', 'host': host.name})

        # we are  now in a situation where there is no more room on runbot_xxx
        # and there is a pending build: build_a_last
        # so we need to make room
        self.Runbot._gc_testing(host)

        # the killable build should have been marked to be killed
        self.assertEqual(build_a.requested_action, 'deathrow')
        self.assertFalse(build_b.requested_action)
        self.assertFalse(build_a_last.requested_action)
        self.assertFalse(children_b.requested_action)


class TestClosestBranch(RunbotCase):

    def branch_description(self, branch):
        branch_type = 'pull' if 'pull' in branch.name else 'branch'
        return '%s %s:%s' % (branch_type, branch.repo_id.name.split(':')[-1], branch.name.split('/')[-1])

    def assertClosest(self, branch, closest):
        extra_repo = branch.repo_id.dependency_ids[0]
        self.assertEqual(closest, branch._get_closest_branch(extra_repo.id), "build on %s didn't had the extected closest branch" % self.branch_description(branch))

    # TODO adapt to project matching
    #def assertDuplicate(self, branch1, branch2, b1_closest=None, b2_closest=None, noDuplicate=False):
    #    """
    #    Test that the creation of a build on branch1 and branch2 detects duplicate, no matter the order.
    #    Also test that build on branch1 closest_branch_name result is b1_closest if given
    #    Also test that build on branch2 closest_branch_name result is b2_closest if given
    #    """
    #    closest = {
    #        branch1: b1_closest,
    #        branch2: b2_closest,
    #    }
    #    for b1, b2 in [(branch1, branch2), (branch2, branch1)]:
    #        hash = '%s%s' % (b1.name, b2.name)
    #        build1 = self.Build.create({
    #            'branch_id': b1.id,
    #            'name': hash,
    #        })

    #        if b1_closest:
    #            self.assertClosest(b1, closest[b1])

    #        build2 = self.Build.create({
    #            'branch_id': b2.id,
    #            'name': hash,
    #        })

    #        if b2_closest:
    #            self.assertClosest(b2, closest[b2])
    #        if noDuplicate:
    #            self.assertNotEqual(build2.local_state, 'duplicate')
    #            self.assertFalse(build2.duplicate_id, "build on %s was detected as duplicate of build %s" % (self.branch_description(b2), build2.duplicate_id))
    #        else:
    #            self.assertEqual(build2.duplicate_id.id, build1.id, "build on %s wasn't detected as duplicate of build on %s" % (self.branch_description(b2), self.branch_description(b1)))
    #            self.assertEqual(build2.local_state, 'duplicate')

    #def assertNoDuplicate(self, branch1, branch2, b1_closest=None, b2_closest=None):
    #    self.assertDuplicate(branch1, branch2, b1_closest=b1_closest, b2_closest=b2_closest, noDuplicate=True)

    #def setUp(self):
    #    """ Setup repositories that mimick the Odoo repos """
    #    super(TestClosestBranch, self).setUp()
    #    self.Repo = self.env['runbot.repo']
    #    self.community_repo = self.Repo.create({'name': 'bla@example.com:odoo/odoo', 'token': '1'})
    #    self.enterprise_repo = self.Repo.create({'name': 'bla@example.com:odoo/enterprise', 'token': '1'})
    #    self.community_dev_repo = self.Repo.create({'name': 'bla@example.com:odoo-dev/odoo', 'token': '1'})
    #    self.enterprise_dev_repo = self.Repo.create({'name': 'bla@example.com:odoo-dev/enterprise', 'token': '1'})
#
    #    # tweak duplicates links between repos
    #    self.community_repo.duplicate_id = self.community_dev_repo.id
    #    self.community_dev_repo.duplicate_id = self.community_repo.id
    #    self.enterprise_repo.duplicate_id = self.enterprise_dev_repo.id
    #    self.enterprise_dev_repo.duplicate_id = self.enterprise_repo.id
#
    #    # create depenedencies to find Odoo server
    #    self.enterprise_repo.dependency_ids = self.community_repo
    #    self.enterprise_dev_repo.dependency_ids = self.community_dev_repo
#
    #    # Create some sticky branches
    #    self.Branch = self.env['runbot.branch']
    #    self.branch_odoo_master = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/heads/master',
    #        'sticky': True,
    #    })
    #    self.branch_odoo_10 = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/heads/10.0',
    #        'sticky': True,
    #    })
    #    self.branch_odoo_11 = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/heads/11.0',
    #        'sticky': True,
    #    })
#
    #    self.branch_enterprise_master = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/heads/master',
    #        'sticky': True,
    #    })
    #    self.branch_enterprise_10 = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/heads/10.0',
    #        'sticky': True,
    #    })
    #    self.branch_enterprise_11 = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/heads/11.0',
    #        'sticky': True,
    #    })
#
    #    self.Build = self.env['runbot.build']
#
    #def test_pr_is_duplicate(self):
    #    """ test PR is a duplicate of a dev branch build """
#
    #    mock_github = self.patchers['github_patcher']
    #    mock_github.return_value = {
    #        'head': {'label': 'odoo-dev:10.0-fix-thing-moc'},
    #        'base': {'ref': '10.0'},
    #        'state': 'open'
    #    }
#
    #    dev_branch = self.Branch.create({
    #        'repo_id': self.community_dev_repo.id,
    #        'name': 'refs/heads/10.0-fix-thing-moc'
    #    })
    #    pr = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/pull/12345'
    #    })
    #    self.assertDuplicate(dev_branch, pr)
#
    #def test_closest_branch_01(self):
    #    """ test find a matching branch in a target repo based on branch name """
#
    #    self.Branch.create({
    #        'repo_id': self.community_dev_repo.id,
    #        'name': 'refs/heads/10.0-fix-thing-moc'
    #    })
    #    addons_branch = self.Branch.create({
    #        'repo_id': self.enterprise_dev_repo.id,
    #        'name': 'refs/heads/10.0-fix-thing-moc'
    #    })
#
    #    self.assertEqual((addons_branch, 'exact'), addons_branch._get_closest_branch(self.enterprise_dev_repo.id))
#
    #def test_closest_branch_02(self):
    #    """ test find two matching PR having the same head name """
#
    #    mock_github = self.patchers['github_patcher']
    #    mock_github.return_value = {
    #        # "head label" is the repo:branch where the PR comes from
    #        # "base ref" is the target of the PR
    #        'head': {'label': 'odoo-dev:bar_branch'},
    #        'base': {'ref': 'saas-12.2'},
    #        'state': 'open'
    #    }
#
    #    # update to avoid test to break. we asume that bar_branch exists.
    #    # we may want to modify the branch creation to ensure that
    #    # -> first make all branches
    #    # -> then make all builds
    #    community_branch = self.Branch.create({
    #        'repo_id': self.community_dev_repo.id,
    #        'name': 'refs/heads/bar_branch'
    #    })
#
    #    # Create PR in community
    #    community_pr = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/pull/123456'
    #    })
    #    enterprise_pr = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/pull/789101'
    #    })
    #    self.assertEqual((community_branch, 'exact PR'), enterprise_pr._get_closest_branch(self.community_repo.id))
#
    #def test_closest_branch_02_improved(self):
    #    """ test that a PR in enterprise with a matching PR in Community
    #    uses the matching one"""
#
    #    mock_github = self.patchers['github_patcher']
#
    #    com_dev_branch = self.Branch.create({
    #        'repo_id': self.community_dev_repo.id,
    #        'name': 'refs/heads/saas-12.2-blabla'
    #    })
#
    #    ent_dev_branch = self.Branch.create({
    #        'repo_id': self.enterprise_dev_repo.id,
    #        'name': 'refs/heads/saas-12.2-blabla'
    #    })
#
    #    def github_side_effect(url, **kwargs):
    #        # "head label" is the repo:branch where the PR comes from
    #        # "base ref" is the target of the PR
    #        if url.endswith('/pulls/3721'):
    #            return {
    #                'head': {'label': 'odoo-dev:saas-12.2-blabla'},
    #                'base': {'ref': 'saas-12.2'},
    #                'state': 'open'
    #            }
    #        elif url.endswith('/pulls/32156'):
    #            return {
    #                'head': {'label': 'odoo-dev:saas-12.2-blabla'},
    #                'base': {'ref': 'saas-12.2'},
    #                'state': 'open'
    #            }
    #        else:
    #            self.assertTrue(False)
#
    #    mock_github.side_effect = github_side_effect
#
    #    ent_pr = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/pull/3721'
    #    })
#
    #    self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/pull/32156'
    #    })
    #    with patch('odoo.addons.runbot.models.repo.Repo._git_rev_parse', new=rev_parse):
    #        self.assertDuplicate(
    #            ent_dev_branch,
    #            ent_pr,
    #            (com_dev_branch, 'exact'),
    #            (com_dev_branch, 'exact PR')
    #        )
#
    #def test_closest_branch_03(self):
    #    """ test find a branch based on dashed prefix"""
    #    addons_branch = self.Branch.create({
    #        'repo_id': self.enterprise_dev_repo.id,
    #        'name': 'refs/heads/10.0-fix-blah-blah-moc'
    #    })
    #    self.assertEqual((self.branch_odoo_10, 'prefix'), addons_branch._get_closest_branch(self.community_repo.id))
#
    #def test_closest_branch_03_05(self):
    #    """ test that a PR in enterprise without a matching PR in Community
    #    and no branch in community"""
    #    mock_github = self.patchers['github_patcher']
    #    # comm_repo = self.repo
    #    # self.repo.write({'token': 1})
#
    #    ent_dev_branch = self.Branch.create({
    #        'repo_id': self.enterprise_dev_repo.id,
    #        'name': 'refs/heads/saas-12.2-blabla'
    #    })
#
    #    def github_side_effect(url, **kwargs):
    #        if url.endswith('/pulls/3721'):
    #            return {
    #                'head': {'label': 'odoo-dev:saas-12.2-blabla'},
    #                'base': {'ref': 'saas-12.2'},
    #                'state': 'open'
    #            }
    #        elif url.endswith('/pulls/32156'):
    #            return {
    #                'head': {'label': 'odoo-dev:saas-12.2-blabla'},
    #                'base': {'ref': 'saas-12.2'},
    #                'state': 'open'
    #            }
    #        else:
    #            self.assertTrue(False)
#
    #    mock_github.side_effect = github_side_effect
#
    #    com_branch = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/heads/saas-12.2'
    #    })
#
    #    ent_pr = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/pull/3721'
    #    })
    #    with patch('odoo.addons.runbot.models.repo.Repo._git_rev_parse', new=rev_parse):
    #        self.assertDuplicate(
    #            ent_pr,
    #            ent_dev_branch,
    #            (com_branch, 'pr_target'),
    #            (com_branch, 'prefix'),
    #        )
#
    #def test_closest_branch_04(self):
    #    """ test that a PR in enterprise without a matching PR in Community
    #    uses the corresponding exact branch in community"""
    #    mock_github = self.patchers['github_patcher']
#
    #    com_dev_branch = self.Branch.create({
    #        'repo_id': self.community_dev_repo.id,
    #        'name': 'refs/heads/saas-12.2-blabla'
    #    })
#
    #    ent_dev_branch = self.Branch.create({
    #        'repo_id': self.enterprise_dev_repo.id,
    #        'name': 'refs/heads/saas-12.2-blabla'
    #    })
#
    #    def github_side_effect(*args, **kwargs):
    #        return {
    #            'head': {'label': 'odoo-dev:saas-12.2-blabla'},
    #            'base': {'ref': 'saas-12.2'},
    #            'state': 'open'
    #        }
#
    #    mock_github.side_effect = github_side_effect
#
    #    ent_pr = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/pull/3721'
    #    })
    #    with patch('odoo.addons.runbot.models.repo.Repo._git_rev_parse', new=rev_parse):
    #        self.assertDuplicate(
    #            ent_dev_branch,
    #            ent_pr,
    #            (com_dev_branch, 'exact'),
    #            (com_dev_branch, 'no PR')
    #        )
#
    #def test_closest_branch_05(self):
    #    """ test last resort value """
    #    mock_github = self.patchers['github_patcher']
    #    mock_github.return_value = {
    #        'head': {'label': 'foo-dev:bar_branch'},
    #        'base': {'ref': '10.0'},
    #        'state': 'open'
    #    }
#
    #    server_pr = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/pull/123456'
    #    })
#
    #    # trigger compute and ensure that mock_github is used. (using correct side effect would work too)
    #    self.assertEqual(server_pr.pull_head_name, 'foo-dev:bar_branch')
#
    #    mock_github.return_value = {
    #        'head': {'label': 'foo-dev:foobar_branch'},
    #        'base': {'ref': '10.0'},
    #        'state': 'open'
    #    }
    #    addons_pr = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/pull/789101'
    #    })
    #    self.assertEqual(addons_pr.pull_head_name, 'foo-dev:foobar_branch')
    #    closest = addons_pr._get_closest_branch(self.community_repo.id)
    #    self.assertEqual((self.branch_odoo_10, 'pr_target'), addons_pr._get_closest_branch(self.community_repo.id))
#
    #def test_closest_branch_05_master(self):
    #    """ test last resort value when nothing common can be found"""
#
    #    addons_branch = self.Branch.create({
    #        'repo_id': self.enterprise_dev_repo.id,
    #        'name': 'refs/head/badref-fix-foo'
    #    })
    #    self.assertEqual((self.branch_odoo_master, 'default'), addons_branch._get_closest_branch(self.community_repo.id))
#
    #def test_no_duplicate_update(self):
    #    """push a dev branch in enterprise with same head as sticky, but with a matching branch in community"""
    #    community_sticky_branch = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/heads/saas-12.2',
    #        'sticky': True,
    #    })
    #    community_dev_branch = self.Branch.create({
    #        'repo_id': self.community_dev_repo.id,
    #        'name': 'refs/heads/saas-12.2-dev1',
    #    })
    #    enterprise_sticky_branch = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/heads/saas-12.2',
    #        'sticky': True,
    #    })
    #    enterprise_dev_branch = self.Branch.create({
    #        'repo_id': self.enterprise_dev_repo.id,
    #        'name': 'refs/heads/saas-12.2-dev1'
    #    })
    #    # we shouldn't have duplicate since community_dev_branch exists
    #    with patch('odoo.addons.runbot.models.repo.Repo._git_rev_parse', new=rev_parse):
    #        # lets create an old enterprise build
    #        self.Build.create({
    #            'branch_id': enterprise_sticky_branch.id,
    #            'name': 'd0d0caca0000ffffffffffffffffffffffffffff',
    #        })
    #        self.assertNoDuplicate(
    #            enterprise_sticky_branch,
    #            enterprise_dev_branch,
    #            (community_sticky_branch, 'exact'),
    #            (community_dev_branch, 'exact'),
    #        )
#
    #def test_external_pr_closest_branch(self):
    #    """ test last resort value target_name"""
    #    mock_github = self.patchers['github_patcher']
    #    mock_github.return_value = {
    #        'head': {'label': 'external_repo:11.0-fix'},
    #        'base': {'ref': '11.0'},
    #        'state': 'open'
    #    }
    #    enterprise_pr = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/pull/123456'
    #    })
    #    dependency_repo = self.enterprise_repo.dependency_ids[0]
    #    closest_branch = enterprise_pr._get_closest_branch(dependency_repo.id)
    #    self.assertEqual(enterprise_pr._get_closest_branch(dependency_repo.id), (self.branch_odoo_11, 'pr_target'))
#
    #def test_external_pr_with_comunity_pr_closest_branch(self):
    #    """ test matching external pr """
    #    mock_github = self.patchers['github_patcher']
    #    mock_github.return_value = {
    #        'head': {'label': 'external_dev_repo:11.0-fix'},
    #        'base': {'ref': '11.0'},
    #        'state': 'open'
    #    }
    #    community_pr = self.Branch.create({
    #        'repo_id': self.community_repo.id,
    #        'name': 'refs/pull/123456'
    #    })
    #    mock_github.return_value = {
    #        'head': {'label': 'external_dev_repo:11.0-fix'}, # if repo doenst match, it wont work, maybe a fix to do here?
    #        'base': {'ref': '11.0'},
    #        'state': 'open'
    #    }
    #    enterprise_pr = self.Branch.create({
    #        'repo_id': self.enterprise_repo.id,
    #        'name': 'refs/pull/123'
    #    })
    #    with patch('odoo.addons.runbot.models.repo.Repo._git_rev_parse', new=rev_parse):
    #        build = self.Build.create({
    #            'branch_id': enterprise_pr.id,
    #            'name': 'd0d0caca0000ffffffffffffffffffffffffffff',
    #        })
    #        dependency_repo = build.repo_id.dependency_ids[0]
    #        self.assertEqual(build.branch_id._get_closest_branch(dependency_repo.id), (community_pr, 'exact PR'))
    #        # this is working here because pull_head_name is set, but on runbot pull_head_name is empty for external pr. why?
