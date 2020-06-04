# -*- coding: utf-8 -*-
from odoo.tools import mute_logger
from .common import RunbotCase

class TestBranch(RunbotCase):

    def test_base_fields(self):
        branch = self.Branch.create({
            'remote_id': self.remote_server.id,
            'name': 'master',
            'is_pr': False,
        })

        self.assertEqual(branch.branch_url, 'https://example.com/base/server/tree/master')

    def test_pull_request(self):
        mock_github = self.patchers['github_patcher']
        mock_github.return_value = {
            'base' : {'ref': 'master'},
            'head' : {'label': 'foo-dev:bar_branch', 'repo': {'full_name': 'foo-dev/bar'}},
        }
        pr = self.Branch.create({
            'remote_id': self.remote_server.id,
            'name': '12345',
            'is_pr': True,
        })
        self.assertEqual(pr.name, '12345')
        #self.assertEqual(pr.branch_name, 'bar_branch') # TODO check juste an idea to recycle branch_name
        self.assertEqual(pr.branch_url, 'https://example.com/base/server/pull/12345')
        self.assertEqual(pr.target_branch_name, 'master')
        self.assertEqual(pr.pull_head_name, 'foo-dev:bar_branch')

    # TODO fix coverage feature?


class TestBranchRelations(RunbotCase):

    def setUp(self):
        super(TestBranchRelations, self).setUp()

        def create_base(name):
            branch = self.Branch.create({
                'remote_id': self.remote_server.id,
                'name': name,
                'is_pr': False,
            })
            branch.bundle_id.is_base = True
            return branch
        self.master = create_base('master')
        create_base('11.0')
        create_base('saas-11.1')
        create_base('12.0')
        create_base('saas-12.3')
        create_base('13.0')
        create_base('saas-13.1')
        self.last = create_base('saas-13.2')

    def test_relations_master_dev(self):
        b = self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': 'master-test-tri',
                'is_pr': False,
            })
        self.assertEqual(b.bundle_id.base_id.name, 'master')
        self.assertEqual(b.bundle_id.previous_version_base_id.name, '13.0')
        self.assertEqual(sorted(b.bundle_id.intermediate_version_base_ids.mapped('name')), ['saas-13.1', 'saas-13.2'])

    def test_relations_master(self):
        b = self.master
        self.assertEqual(b.bundle_id.base_id.name, 'master')
        self.assertEqual(b.bundle_id.previous_version_base_id.name, '13.0')
        self.assertEqual(sorted(b.bundle_id.intermediate_version_base_ids.mapped('name')), ['saas-13.1', 'saas-13.2'])

    def test_relations_no_intermediate(self):
        b = self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': 'saas-13.1-test-tri',
                'is_pr': False,
            })
        self.assertEqual(b.bundle_id.base_id.name, 'saas-13.1')
        self.assertEqual(b.bundle_id.previous_version_base_id.name, '13.0')
        self.assertEqual(sorted(b.bundle_id.intermediate_version_base_ids.mapped('name')), [])

    def test_relations_old_branch(self):
        b = self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': '11.0-test-tri',
                'is_pr': False,
            })
        self.assertEqual(b.bundle_id.base_id.name, '11.0')
        self.assertEqual(b.bundle_id.previous_version_base_id.name, False)
        self.assertEqual(sorted(b.bundle_id.intermediate_version_base_ids.mapped('name')), [])

    def test_relations_closest_forced(self):
        b = self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': 'master-test-tri',
                'is_pr': False,
            })
        self.assertEqual(b.bundle_id.base_id.name, 'master')
        self.assertEqual(b.bundle_id.previous_version_base_id.name, '13.0')
        self.assertEqual(sorted(b.bundle_id.intermediate_version_base_ids.mapped('name')), ['saas-13.1', 'saas-13.2'])

        b.bundle_id.defined_base_id = self.last.bundle_id

        self.assertEqual(b.bundle_id.base_id.name, 'saas-13.2')
        self.assertEqual(b.bundle_id.previous_version_base_id.name, '13.0')
        self.assertEqual(sorted(b.bundle_id.intermediate_version_base_ids.mapped('name')), ['saas-13.1'])

    def test_relations_no_match(self):
        b = self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': 'icantnamemybranches',
                'is_pr': False,
            })

        self.assertEqual(b.bundle_id.base_id.name, 'master')

    def test_relations_pr(self):
        self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': 'master-test-tri',
                'is_pr': False,
            })

        self.patchers['github_patcher'].return_value = {
            'base': {'ref': 'master-test-tri'},
            'head': {'label': 'dev:master-test-tri-imp', 'repo': {'full_name': 'dev/server'}},
            }
        b = self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': '100',
                'is_pr': True,
            })

        self.assertEqual(b.bundle_id.name, 'master-test-tri-imp')
        self.assertEqual(b.bundle_id.base_id.name, 'master')
        self.assertEqual(b.bundle_id.previous_version_base_id.name, '13.0')
        self.assertEqual(sorted(b.bundle_id.intermediate_version_base_ids.mapped('name')), ['saas-13.1', 'saas-13.2'])


class TestBranchForbidden(RunbotCase):
    """Test that a branch matching the repo forbidden regex, goes to dummy bundle"""

    def test_forbidden(self):
        dummy_bundle = self.env.ref('runbot.bundle_dummy')
        self.remote_server_dev.repo_id.forbidden_regex = '^bad_name.+'
        with mute_logger("odoo.addons.runbot.models.branch"):
            branch = self.Branch.create({
                    'remote_id': self.remote_server_dev.id,
                    'name': 'bad_name-evil',
                    'is_pr': False,
                })
            self.assertEqual(branch.bundle_id.id, dummy_bundle.id, "A forbidden branch should goes in dummy bundle")


class TestBranchIsBase(RunbotCase):
    """Test that a branch matching the is_base_regex goes in the right bundle"""

    def setUp(self):
        super(TestBranchIsBase, self).setUp()
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('runbot.runbot_is_base_regex', r'^(saas-)?\d+\.\d+$')

    def test_is_base_regex_on_main_remote(self):
        branch = self.Branch.create({
                'remote_id': self.remote_server.id,
                'name': 'saas-13.4',
                'is_pr': False,
            })
        self.assertTrue(branch.bundle_id.is_base, "A branch matching the is_base_regex parameter should create is_base bundle")
        self.assertTrue(branch.bundle_id.sticky, "A branch matching the is_base_regex parameter should create sticky bundle")

    @mute_logger("odoo.addons.runbot.models.branch")
    def test_is_base_regex_on_dev_remote(self):
        dummy_bundle = self.env.ref('runbot.bundle_dummy')
        branch = self.Branch.create({
                'remote_id': self.remote_server_dev.id,
                'name': 'saas-13.4',
                'is_pr': False,
            })
        self.assertEqual(branch.bundle_id.id, dummy_bundle.id, "A branch matching the is_base_regex should on a secondary repo should goes in dummy bundle")
