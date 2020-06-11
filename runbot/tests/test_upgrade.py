from .common import RunbotCaseMinimalSetup
from odoo.tests.common import tagged
from odoo.exceptions import UserError

#@tagged('post_install')
class TestUpgrade(RunbotCaseMinimalSetup):

    def setUp(self):
        super().setUp()
        # TODO test trigger upgrade
        self.minimal_setup()

        self.nightly_category = self.env.ref('runbot.nightly_category')

        self.repo_upgrade = self.env['runbot.repo'].create({
            'name': 'upgrade',
            'project_id': self.project.id,
        })

        self.remote_upgrade = self.env['runbot.remote'].create({
            'name': 'bla@example.com:base/upgrade',
            'repo_id': self.repo_upgrade.id,
            'token': '123',
        })

        self.step_upgrade_server = self.env['runbot.build.config.step'].create({
            'name': 'upgrade_server',
            'job_type': 'upgrade',

            'upgrade_from_previous_version': True,
            'upgrade_from_last_intermediate_version': True,
            'upgrade_from_all_intermediate_version': False,

        })
        self.upgrade_server_config = self.env['runbot.build.config'].create({
            'name': 'Upgrade server',
            'step_order_ids':[(0, 0, {'step_id': self.step_upgrade_server.id})]
        })

        self.config_nightly = self.env['runbot.build.config'].create({'name': 'Nightly config'})
        self.config_nightly_db_generate = self.env['runbot.build.config'].create({'name': 'Nightly generate'})
        self.config_all = self.env['runbot.build.config'].create({'name': 'Demo'})
        self.config_all_no_demo = self.env['runbot.build.config'].create({'name': 'No demo'})

        self.trigger_server_nightly = self.env['runbot.trigger'].create({
            'name': 'Nighly server',
            'dependency_ids': [(4, self.repo_server.id)],
            'config_id': self.config_nightly.id,
            'project_id': self.project.id,
            'category_id': self.nightly_category.id
        })

        self.trigger_addons_nightly = self.env['runbot.trigger'].create({
            'name': 'Nighly addons',
            'dependency_ids': [(4, self.repo_server.id), (4, self.repo_addons.id)],
            'config_id': self.config_nightly.id,
            'project_id': self.project.id,
            'category_id': self.nightly_category.id
        })

        self.trigger_upgrade_server = self.env['runbot.trigger'].create({
            'name': 'Server upgrade',
            'repo_ids': [(4, self.repo_upgrade.id), (4, self.repo_server.id)],
            'config_id': self.upgrade_server_config.id,
            'project_id': self.project.id,
            'upgrade_dumps_trigger_id': self.trigger_server_nightly.id,
        })
        self.assertEqual(self.trigger_upgrade_server.upgrade_step_id, self.step_upgrade_server)

        self.branch_upgrade = self.Branch.create({
            'name': 'master',
            'remote_id': self.remote_upgrade.id,
            'is_pr': False,
            'head': self.Commit.create({
                'name': '123abc789',
                'repo_id': self.repo_upgrade.id,
            }).id,
        })
        self.assertEqual(self.branch_server.bundle_id, self.branch_upgrade.bundle_id)
        self.assertTrue(self.branch_upgrade.bundle_id.is_base)
        self.assertTrue(self.branch_upgrade.bundle_id.version_id)


    def create_version(self, name):
        intname = int(''.join(c for c in name if c.isdigit()))
        branch_server = self.Branch.create({
            'name': name,
            'remote_id': self.remote_server.id,
            'is_pr': False,
            'head': self.Commit.create({
                'name': 'server%s' % intname,
                'repo_id': self.repo_server.id,
            }).id,
        })
        branch_addons = self.Branch.create({
            'name': name,
            'remote_id': self.remote_addons.id,
            'is_pr': False,
            'head': self.Commit.create({
                'name': 'addons%s' % intname,
                'repo_id': self.repo_addons.id,
            }).id,
        })
        self.assertEqual(branch_server.bundle_id, branch_addons.bundle_id)
        bundle = branch_server.bundle_id
        self.assertEqual(bundle.name, name)
        bundle.is_base = True
        # create nightly
        batch = bundle._force(self.nightly_category.id)
        self.assertEqual(batch.category_id, self.nightly_category)
        builds = {}
        for build in batch.slot_ids.mapped('build_id'):
            self.assertEqual(build.params_id.config_id, self.config_nightly)
            main_child = build._add_child({'config_id': self.config_nightly_db_generate.id})
            demo = main_child._add_child({'config_id': self.config_all.id})
            no_demo = main_child._add_child({'config_id': self.config_all_no_demo.id})
            (build | main_child | demo | no_demo).write({'local_state': 'done'})
            builds[('root', build.params_id.trigger_id)] = build
            builds[('demo', build.params_id.trigger_id)] = demo
            builds[('no_demo', build.params_id.trigger_id)] = no_demo
        batch.state = 'done'
        return batch, builds

    def test_ensure_config_step_upgrade(self):
        with self.assertRaises(UserError):
            self.step_upgrade_server.job_type = 'install_odoo'
            self.step_upgrade_server.flush()

    def test_dependency_builds(self):
        _, build_niglty_13 = self.create_version('13.0')
        _, build_niglty_131 = self.create_version('saas-13.1')
        _, build_niglty_132 = self.create_version('saas-13.2')
        _, build_niglty_133 = self.create_version('saas-13.3')

        batch = self.branch_upgrade.bundle_id._force()
        upgrade_slot = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade_server)
        self.assertTrue(upgrade_slot)
        upgrade_build = upgrade_slot.build_id
        self.assertTrue(upgrade_build)
        self.assertEqual(upgrade_build.params_id.config_id, self.upgrade_server_config)
        #e should have 2 builds, the nightly roots of 13 and 13.3
        self.assertEqual(
            batch.slot_ids.mapped('build_id.params_id.builds_reference_ids'),
            (
                build_niglty_13[('root', self.trigger_server_nightly)] |
                build_niglty_133[('root', self.trigger_server_nightly)]
            )
        )

        self.trigger_upgrade_server.upgrade_step_id.upgrade_from_all_intermediate_version = True
        batch = self.branch_upgrade.bundle_id._force()
        upgrade_build = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade_server).build_id
        self.assertEqual(
            batch.slot_ids.mapped('build_id.params_id.builds_reference_ids'),
            (
                build_niglty_13[('root', self.trigger_server_nightly)] |
                build_niglty_131[('root', self.trigger_server_nightly)] |
                build_niglty_132[('root', self.trigger_server_nightly)] |
                build_niglty_133[('root', self.trigger_server_nightly)]
            )
        )

    #def test_migration_step(self):
    #    # TODO test difference sticky/base
    #    _, build_niglty_11 = self.create_version('11.0')
    #    _, build_niglty_113 = self.create_version('saas-11.3')
    #    _, build_niglty_12 = self.create_version('12.0')
    #    _, build_niglty_123 =self.create_version('saas-12.3')
    #    _, build_niglty_13 = self.create_version('13.0')
    #    _, build_niglty_131 = self.create_version('saas-13.1')
    #    _, build_niglty_132 = self.create_version('saas-13.2')
    #    _, build_niglty_133 = self.create_version('saas-13.3')
    #    batch = self.branch_upgrade.bundle_id._force()
    #    upgrade_build = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade_server).build_id
    #    upgrade_build._schedule()
