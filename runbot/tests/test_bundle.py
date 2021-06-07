# -*- coding: utf-8 -*-
from .common import RunbotCase

class TestBundle(RunbotCase):

    def test_pull_request_labels(self):
        mock_github = self.patchers['github_patcher']
        mock_github.return_value = {
            'base': {'ref': 'master'},
            'head': {'label': 'foo-dev:bar_branch', 'repo': {'full_name': 'dev/server'}},
            'labels': [{
                'name': 'test 14.4',
                'color': 'ededed',
            }, {
                'name': 'test forwardport',
                'color': 'a4fcde',
            }]
        }

        # create a pre-existing label to test unique constraint
        self.env['runbot.branch.label'].create([{'name': 'test forwardport'}])

        # create a dev branch and a PR
        dev_branch = self.Branch.create({
            'remote_id': self.remote_server_dev.id,
            'name': 'bar_branch',
            'is_pr': False
        })

        pr = self.Branch.create({
            'remote_id': self.remote_server.id,
            'name': '12345',
            'is_pr': True,
        })
        self.assertEqual(pr.name, '12345')
        self.assertEqual(pr.branch_url, 'https://example.com/base/server/pull/12345')
        self.assertEqual(pr.target_branch_name, 'master')
        self.assertEqual(pr.pull_head_name, 'foo-dev:bar_branch')

        # test that the bundle was created with branch and PR
        bundle = dev_branch.bundle_id
        self.assertIn(pr, bundle.branch_ids)

        # test the labels
        labels = self.env['runbot.branch.label'].search([('name', 'like', 'test %')])
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels, pr.label_ids)
        self.assertEqual(bundle.labels, pr.label_ids)

        # create an addon branch and Pr
        mock_github.return_value = {
            'base': {'ref': 'master'},
            'head': {'label': 'foo-dev:bar_branch', 'repo': {'full_name': 'dev/addons'}},
            'labels': [{
                'name': 'foo label',
                'color': 'ededed',
            }, {
                'name': 'test forwardport',
                'color': 'a4fcde',
            }]
        }

        addon_dev_branch = self.Branch.create({
            'remote_id': self.remote_addons_dev.id,
            'name': 'bar_branch',
            'is_pr': False
        })
        self.assertEqual(addon_dev_branch.bundle_id, bundle)

        addon_pr = self.Branch.create({
            'remote_id': self.remote_addons.id,
            'name': '6789',
            'is_pr': True,
        })
        self.assertEqual(addon_pr.bundle_id, bundle)
        self.assertEqual(len(bundle.branch_ids), 4)

        # now test that labels are correctly set on the bundle
        self.assertEqual(3, len(bundle.labels))
        self.assertIn('foo label', bundle.labels.mapped('name'))
        self.assertIn('test forwardport', bundle.labels.mapped('name'))
        self.assertIn('test 14.4', bundle.labels.mapped('name'))

        # check that bundle labels can be searched
        fw_port_bundle_ids = self.env['runbot.bundle'].search([('labels', '=', 'test forwardport')])
        self.assertEqual(fw_port_bundle_ids, bundle)
        test_bundle_ids = self.env['runbot.bundle'].search([('labels', 'ilike', 'test%')])
        self.assertEqual(test_bundle_ids, bundle)
        foo_bundle_ids = self.env['runbot.bundle'].search([('labels', 'in', ['test forwardport', 'foo label'])])
        self.assertEqual(foo_bundle_ids, bundle)

        # check labels bundle
        fw_label = self.env['runbot.branch.label'].search([('name', '=', 'test forwardport')])
        self.assertIn(bundle, fw_label.bundle_ids)

    def test_pull_request_pr_state(self):
        # create a dev branch
        dev_branch = self.Branch.create({
            'remote_id': self.remote_server_dev.id,
            'name': 'bar_branch',
            'is_pr': False
        })

        bundle = dev_branch.bundle_id
        self.assertEqual(bundle.name, 'bar_branch')
        self.assertEqual(bundle.pr_state, 'nopr', 'The bundle should be in `nopr` state')
        self.assertIn(bundle, self.Bundle.search([('pr_state', '=', 'nopr')]))

        # now create a PR and check that the bundle pr_state is `open`
        mock_github = self.patchers['github_patcher']
        mock_github.return_value = {
            'base': {'ref': 'master'},
            'head': {'label': 'foo-dev:bar_branch', 'repo': {'full_name': 'dev/server'}},
        }

        pr = self.Branch.create({
            'remote_id': self.remote_server.id,
            'name': '12345',
            'is_pr': True,
            'alive': True
        })

        self.env['runbot.branch'].flush()  # Needed to test sql query in _search_pr_state

        self.assertIn(pr, bundle.branch_ids)
        self.assertEqual(bundle.pr_state, 'open')
        self.assertIn(bundle, self.Bundle.search([('pr_state', '=', 'open')]))
        self.assertNotIn(bundle, self.Bundle.search([('pr_state', '=', 'done')]))

        # add a new PR from another repo in the same bundle (mimic enterprise PR)
        mock_github.return_value = {
            'base': {'ref': 'master'},
            'head': {'label': 'bar-dev:bar_branch', 'repo': {'full_name': 'dev/addons'}},
        }

        addons_pr = self.Branch.create({
            'remote_id': self.remote_addons.id,
            'name': '6789',
            'is_pr': True,
            'alive': True
        })

        self.assertIn(addons_pr, bundle.branch_ids)
        self.assertEqual(bundle.pr_state, 'open')
        self.assertIn(bundle, self.Bundle.search([('pr_state', '=', 'open')]))
        self.assertNotIn(bundle, self.Bundle.search([('pr_state', '=', 'done')]))

        # one PR is closed, the bundle pr_state should stay open
        addons_pr.alive = False
        self.env['runbot.branch'].flush()  # Needed to test sql query in _search_pr_state
        self.assertEqual(bundle.pr_state, 'open')
        self.assertIn(bundle, self.Bundle.search([('pr_state', '=', 'open')]))
        self.assertNotIn(bundle, self.Bundle.search([('pr_state', '=', 'done')]))

        # The last PR is closed so the bundle pr state should be done
        pr.alive = False
        self.env['runbot.branch'].flush()  # Needed to test sql query in _search_pr_state
        self.assertEqual(bundle.pr_state, 'done')
        self.assertNotIn(bundle, self.Bundle.search([('pr_state', '=', 'open')]))
        self.assertIn(bundle, self.Bundle.search([('pr_state', '=', 'done')]))
