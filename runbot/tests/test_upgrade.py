from .common import RunbotCase, RunbotCaseMinimalSetup
from odoo.tests.common import tagged
from odoo.exceptions import UserError


class TestVersions(RunbotCase):
    def test_version_relations(self):
        version = self.env['runbot.version']
        v11 = version._get('11.0')
        v113 = version._get('saas-11.3')
        v12 = version._get('12.0')
        v122 = version._get('saas-12.2')
        v124 = version._get('saas-12.4')
        v13 = version._get('13.0')
        v131 = version._get('saas-13.1')
        v132 = version._get('saas-13.2')
        v133 = version._get('saas-13.3')
        master = version._get('master')

        self.assertEqual(v11.previous_major_version_id, version)
        self.assertEqual(v11.intermediate_version_ids, version)

        self.assertEqual(v113.previous_major_version_id, v11)
        self.assertEqual(v113.intermediate_version_ids, version)

        self.assertEqual(v12.previous_major_version_id, v11)
        self.assertEqual(v12.intermediate_version_ids, v113)

        self.assertEqual(v12.previous_major_version_id, v11)
        self.assertEqual(v12.intermediate_version_ids, v113)

        self.assertEqual(v13.previous_major_version_id, v12)
        self.assertEqual(v13.intermediate_version_ids, v124|v122)

        self.assertEqual(v132.previous_major_version_id, v13)
        self.assertEqual(v132.intermediate_version_ids, v131)

        self.assertEqual(master.previous_major_version_id, v13)
        self.assertEqual(master.intermediate_version_ids, v133|v132|v131)


class TestUpgrade(RunbotCaseMinimalSetup):

    def setUp(self):
        super().setUp()
        # TODO test trigger upgrade
        self.minimal_setup()

        #################
        # upgrade branch
        #################
        self.repo_upgrade = self.env['runbot.repo'].create({
            'name': 'upgrade',
            'project_id': self.project.id,
        })
        self.remote_upgrade = self.env['runbot.remote'].create({
            'name': 'bla@example.com:base/upgrade',
            'repo_id': self.repo_upgrade.id,
            'token': '123',
        })
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
        self.master_bundle = self.branch_server.bundle_id

        #######################
        # Basic upgrade config
        #######################
        self.step_restore = self.env['runbot.build.config.step'].create({
            'name': 'restore',
            'job_type': 'restore',
        })
        self.step_upgrade = self.env['runbot.build.config.step'].create({
            'name': 'test_upgrade',
            'job_type': 'test_upgrade',
        })
        self.upgrade_config = self.env['runbot.build.config'].create({
            'name': 'Upgrade server',
            'step_order_ids':[
                (0, 0, {'step_id': self.step_restore.id}),
                (0, 0, {'step_id': self.step_upgrade.id})
            ]
        })

        ##########
        # Nightly
        ##########
        self.nightly_category = self.env.ref('runbot.nightly_category')
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

        ##########
        # Weekly
        ##########
        self.weekly_category = self.env.ref('runbot.weekly_category')
        self.config_weekly = self.env['runbot.build.config'].create({'name': 'Nightly config'})
        self.config_single = self.env['runbot.build.config'].create({'name': 'Single'})
        self.trigger_server_weekly = self.env['runbot.trigger'].create({
            'name': 'Nighly server',
            'dependency_ids': [(4, self.repo_server.id)],
            'config_id': self.config_weekly.id,
            'project_id': self.project.id,
            'category_id': self.weekly_category.id
        })
        self.trigger_addons_weekly = self.env['runbot.trigger'].create({
            'name': 'Nighly addons',
            'dependency_ids': [(4, self.repo_server.id), (4, self.repo_addons.id)],
            'config_id': self.config_weekly.id,
            'project_id': self.project.id,
            'category_id': self.weekly_category.id
        })

        ########################################
        # Configure upgrades for 'to current' version
        ########################################
        master = self.env['runbot.version']._get('master')
        self.step_upgrade_server = self.env['runbot.build.config.step'].create({
            'name': 'upgrade_server',
            'job_type': 'configure_upgrade',
            'upgrade_to_current': True,
            'upgrade_from_previous_major_version': True,
            'upgrade_from_last_intermediate_version': True,
            'upgrade_flat': True,
            'upgrade_config_id': self.upgrade_config.id,
            'upgrade_dbs': [
                (0, 0, {'config_id': self.config_all.id, 'db_name': 'all', 'min_target_version_id': master.id}),
                (0, 0, {'config_id': self.config_all_no_demo.id, 'db_name': 'no-demo-all'})
            ]
        })
        self.upgrade_server_config = self.env['runbot.build.config'].create({
            'name': 'Upgrade server',
            'step_order_ids':[(0, 0, {'step_id': self.step_upgrade_server.id})]
        })
        self.trigger_upgrade_server = self.env['runbot.trigger'].create({
            'name': 'Server upgrade',
            'repo_ids': [(4, self.repo_upgrade.id), (4, self.repo_server.id)],
            'config_id': self.upgrade_server_config.id,
            'project_id': self.project.id,
            'upgrade_dumps_trigger_id': self.trigger_server_nightly.id,
        })

        self.assertEqual(self.trigger_upgrade_server.upgrade_step_id, self.step_upgrade_server)

        ########################################
        # Configure upgrades for previouses versions
        ########################################
        self.step_upgrade = self.env['runbot.build.config.step'].create({
            'name': 'upgrade',
            'job_type': 'configure_upgrade',
            'upgrade_to_major_versions': True,
            'upgrade_from_previous_major_version': True,
            'upgrade_flat': True,
            'upgrade_config_id': self.upgrade_config.id,
            'upgrade_dbs': [
                (0, 0, {'config_id': self.config_all.id, 'db_name': 'all', 'min_target_version_id': master.id}),
                (0, 0, {'config_id': self.config_all_no_demo.id, 'db_name': 'no-demo-all'})
            ]
        })
        self.upgrade_config = self.env['runbot.build.config'].create({
            'name': 'Upgrade',
            'step_order_ids':[(0, 0, {'step_id': self.step_upgrade.id})]
        })
        self.trigger_upgrade = self.env['runbot.trigger'].create({
            'name': 'Upgrade',
            'repo_ids': [(4, self.repo_upgrade.id)],
            'config_id': self.upgrade_config.id,
            'project_id': self.project.id,
            'upgrade_dumps_trigger_id': self.trigger_addons_nightly.id,
        })

    def create_version(self, name):
        intname = int(''.join(c for c in name if c.isdigit())) if name != 'master' else 0
        if name != 'master':
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
        else:
            branch_server = self.branch_server
            branch_addons = self.branch_addons

        self.assertEqual(branch_server.bundle_id, branch_addons.bundle_id)
        bundle = branch_server.bundle_id
        self.assertEqual(bundle.name, name)
        bundle.is_base = True
        # create nightly

        batch_nigthly = bundle._force(self.nightly_category.id)
        self.assertEqual(batch_nigthly.category_id, self.nightly_category)
        builds_nigthly  = {}
        for build in batch_nigthly.slot_ids.mapped('build_id'):
            self.assertEqual(build.params_id.config_id, self.config_nightly)
            main_child = build._add_child({'config_id': self.config_nightly_db_generate.id})
            demo = main_child._add_child({'config_id': self.config_all.id})
            demo.database_ids = [
                (0, 0, {'name': '%s-%s' % (demo.dest, 'base')}), 
                (0, 0, {'name': '%s-%s' % (demo.dest, 'dummy')}),
                (0, 0, {'name': '%s-%s' % (demo.dest, 'all')})]
            no_demo = main_child._add_child({'config_id': self.config_all_no_demo.id})
            no_demo.database_ids = [
                (0, 0, {'name': '%s-%s' % (no_demo.dest, 'base')}),
                (0, 0, {'name': '%s-%s' % (no_demo.dest, 'dummy')}),
                (0, 0, {'name': '%s-%s' % (no_demo.dest, 'no-demo-all')})]
            (build | main_child | demo | no_demo).write({'local_state': 'done'})
            builds_nigthly[('root', build.params_id.trigger_id)] = build
            builds_nigthly[('demo', build.params_id.trigger_id)] = demo
            builds_nigthly[('no_demo', build.params_id.trigger_id)] = no_demo
        batch_nigthly.state = 'done'


        batch_weekly = bundle._force(self.weekly_category.id)
        self.assertEqual(batch_weekly.category_id, self.weekly_category)
        builds_weekly  = {}
        for build in batch_weekly.slot_ids.mapped('build_id'):
            build.database_ids = [(0, 0, {'name': '%s-%s' % (build.dest, 'dummy')})]
            self.assertEqual(build.params_id.config_id, self.config_weekly)
            builds_weekly[('root', build.params_id.trigger_id)] = build
            for db in ['l10n_be', 'l10n_ch', 'mail', 'account', 'stock']:
                child = build._add_child({'config_id': self.config_single.id})
                child.database_ids = [(0, 0, {'name': '%s-%s' % (child.dest, db)})]
                child.local_state = 'done'
                builds_weekly[(db, build.params_id.trigger_id)] = child
            build.local_state = 'done'
        batch_weekly.state = 'done'
        return builds_nigthly, builds_weekly

    def test_ensure_config_step_upgrade(self):
        with self.assertRaises(UserError):
            self.step_upgrade_server.job_type = 'install_odoo'
            self.step_upgrade_server.flush()

    def test_dependency_builds(self):
        build_niglty_13, _ = self.create_version('13.0')
        build_niglty_131, _ = self.create_version('saas-13.1')
        build_niglty_132, _ = self.create_version('saas-13.2')
        build_niglty_133, _ = self.create_version('saas-13.3')

        batch = self.master_bundle._force()
        upgrade_slot = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade_server)
        self.assertTrue(upgrade_slot)
        upgrade_build = upgrade_slot.build_id
        self.assertTrue(upgrade_build)
        self.assertEqual(upgrade_build.params_id.config_id, self.upgrade_server_config)
        #e should have 2 builds, the nightly roots of 13 and 13.3
        self.assertEqual(
            upgrade_build.params_id.builds_reference_ids,
            (
                build_niglty_13[('root', self.trigger_server_nightly)] |
                build_niglty_133[('root', self.trigger_server_nightly)]
            )
        )

        self.trigger_upgrade_server.upgrade_step_id.upgrade_from_all_intermediate_version = True
        batch = self.master_bundle._force()
        upgrade_build = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade_server).build_id
        self.assertEqual(
            upgrade_build.params_id.builds_reference_ids,
            (
                build_niglty_13[('root', self.trigger_server_nightly)] |
                build_niglty_131[('root', self.trigger_server_nightly)] |
                build_niglty_132[('root', self.trigger_server_nightly)] |
                build_niglty_133[('root', self.trigger_server_nightly)]
            )
        )

    def test_configure_upgrade_step(self):
        # TODO test difference sticky/base
        build_niglty_master, build_weekly_master = self.create_version('master')
        build_niglty_11, build_weekly_11 = self.create_version('11.0')
        build_niglty_113, build_weekly_113 = self.create_version('saas-11.3')
        build_niglty_12, build_weekly_12 = self.create_version('12.0')
        build_niglty_123, build_weekly_123 = self.create_version('saas-12.3')
        build_niglty_13, build_weekly_13 = self.create_version('13.0')
        build_niglty_131, build_weekly_131 = self.create_version('saas-13.1')
        build_niglty_132, build_weekly_132 = self.create_version('saas-13.2')
        build_niglty_133, build_weekly_133 = self.create_version('saas-13.3')

        # server/addons repos tests
        batch = self.master_bundle._force()
        upgrade_current_build = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade_server).build_id
        host = self.env['runbot.host']._get_current()
        upgrade_current_build.host = host.name
        upgrade_current_build._init_pendings(host)
        upgrade_current_build._schedule()
        self.assertEqual(upgrade_current_build.local_state, 'done')
        self.assertEqual(len(upgrade_current_build.children_ids), 4)

        [b_13_master_demo, b_13_master_no_demo, b_133_master_demo, b_133_master_no_demo] = upgrade_current_build.children_ids

        self.assertEqual(b_13_master_demo.params_id.upgrade_to_build_id, upgrade_current_build)
        self.assertEqual(b_13_master_demo.params_id.upgrade_from_build_id, build_niglty_13[('root', self.trigger_server_nightly)])
        self.assertEqual(b_13_master_demo.params_id.dump_db.build_id, build_niglty_13[('demo', self.trigger_server_nightly)])
        self.assertEqual(b_13_master_demo.params_id.dump_db.name, '%s-all' % b_13_master_demo.params_id.dump_db.build_id.dest)

        self.assertEqual(b_13_master_no_demo.params_id.upgrade_to_build_id, upgrade_current_build)
        self.assertEqual(b_13_master_no_demo.params_id.upgrade_from_build_id, build_niglty_13[('root', self.trigger_server_nightly)])
        self.assertEqual(b_13_master_no_demo.params_id.dump_db.build_id, build_niglty_13[('no_demo', self.trigger_server_nightly)])
        self.assertEqual(b_13_master_no_demo.params_id.dump_db.name, '%s-no-demo-all' % b_13_master_no_demo.params_id.dump_db.build_id.dest)

        self.assertEqual(b_133_master_demo.params_id.upgrade_to_build_id, upgrade_current_build)
        self.assertEqual(b_133_master_demo.params_id.upgrade_from_build_id, build_niglty_133[('root', self.trigger_server_nightly)])
        self.assertEqual(b_133_master_demo.params_id.dump_db.build_id, build_niglty_133[('demo', self.trigger_server_nightly)])
        self.assertEqual(b_133_master_demo.params_id.dump_db.name, '%s-all' % b_133_master_demo.params_id.dump_db.build_id.dest)

        self.assertEqual(b_133_master_no_demo.params_id.upgrade_to_build_id, upgrade_current_build)
        self.assertEqual(b_133_master_no_demo.params_id.upgrade_from_build_id, build_niglty_133[('root', self.trigger_server_nightly)])
        self.assertEqual(b_133_master_no_demo.params_id.dump_db.build_id, build_niglty_133[('no_demo', self.trigger_server_nightly)])
        self.assertEqual(b_133_master_no_demo.params_id.dump_db.name, '%s-no-demo-all' % b_133_master_no_demo.params_id.dump_db.build_id.dest)

        # upgrade repos tests
        upgrade_build = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade).build_id
        host = self.env['runbot.host']._get_current()
        upgrade_build.host = host.name
        upgrade_build._init_pendings(host)
        upgrade_build._schedule()
        self.assertEqual(upgrade_build.local_state, 'done')
        self.assertEqual(len(upgrade_build.children_ids), 2)

        [b_11_12, b_12_13] = upgrade_build.children_ids

        self.assertEqual(b_11_12.params_id.upgrade_to_build_id, build_niglty_12[('root', self.trigger_addons_nightly)])
        self.assertEqual(b_11_12.params_id.upgrade_from_build_id, build_niglty_11[('root', self.trigger_addons_nightly)])
        self.assertEqual(b_11_12.params_id.dump_db.build_id, build_niglty_11[('no_demo', self.trigger_addons_nightly)])
        self.assertEqual(b_11_12.params_id.dump_db.name, '%s-no-demo-all' % b_11_12.params_id.dump_db.build_id.dest)

        self.assertEqual(b_12_13.params_id.upgrade_to_build_id, build_niglty_13[('root', self.trigger_addons_nightly)])
        self.assertEqual(b_12_13.params_id.upgrade_from_build_id, build_niglty_12[('root', self.trigger_addons_nightly)])
        self.assertEqual(b_12_13.params_id.dump_db.build_id, build_niglty_12[('no_demo', self.trigger_addons_nightly)])
        self.assertEqual(b_12_13.params_id.dump_db.name, '%s-no-demo-all' % b_12_13.params_id.dump_db.build_id.dest)

        # upgrade repos nightly


        self.step_upgrade_nightly = self.env['runbot.build.config.step'].create({
            'name': 'upgrade_nightly',
            'job_type': 'configure_upgrade',
            'upgrade_to_master': True,
            'upgrade_to_major_versions': True,
            'upgrade_from_previous_major_version': True,
            'upgrade_from_all_intermediate_version': True,
            'upgrade_flat': False,
            'upgrade_config_id': self.upgrade_config.id,
            'upgrade_dbs': [
                (0, 0, {'config_id': self.config_single.id, 'db_name': '*'})
            ]
        })
        self.upgrade_config_nightly = self.env['runbot.build.config'].create({
            'name': 'Upgrade nightly',
            'step_order_ids':[(0, 0, {'step_id': self.step_upgrade_nightly.id})]
        })
        self.trigger_upgrade_addons_nightly = self.env['runbot.trigger'].create({
            'name': 'Nigtly upgrade',
            'config_id': self.upgrade_config_nightly.id,
            'project_id': self.project.id,
            'dependency_ids': [(4, self.repo_upgrade.id)],
            'upgrade_dumps_trigger_id': self.trigger_addons_weekly.id,
            'category_id': self.nightly_category.id
        })

        batch = self.master_bundle._force(self.nightly_category.id)
        upgrade_nightly = batch.slot_ids.filtered(lambda slot: slot.trigger_id == self.trigger_upgrade_addons_nightly).build_id
        host = self.env['runbot.host']._get_current()
        upgrade_nightly.host = host.name
        upgrade_nightly._init_pendings(host)
        upgrade_nightly._schedule()
        to_version_builds = upgrade_nightly.children_ids
        self.assertEqual(upgrade_nightly.local_state, 'done')
        self.assertEqual(len(to_version_builds), 4)
        self.assertEqual(to_version_builds.mapped(
            'params_id.upgrade_to_build_id.params_id.version_id.name'), 
            ['11.0', '12.0', '13.0', 'master'])
        self.assertEqual(to_version_builds.mapped(
            'params_id.upgrade_from_build_id.params_id.version_id.name'), 
            [])
        to_version_builds.host = host.name
        to_version_builds._init_pendings(host)
        to_version_builds._schedule()
        self.assertEqual(to_version_builds.mapped('local_state'), ['done']*4)
        from_version_builds = to_version_builds.children_ids
        from_to = ['%s->%s' % (b.params_id.upgrade_from_build_id.params_id.version_id.name, b.params_id.upgrade_to_build_id.params_id.version_id.name)
                    for b in from_version_builds]
        self.assertEqual(
            from_to,
            ['11.0->12.0', 'saas-11.3->12.0', '12.0->13.0', 'saas-12.3->13.0', '13.0->master', 'saas-13.1->master', 'saas-13.2->master', 'saas-13.3->master']
        )
        from_version_builds.host = host.name
        from_version_builds._init_pendings(host)
        from_version_builds._schedule()
        self.assertEqual(from_version_builds.mapped('local_state'), ['done']*8)
        db_builds = from_version_builds.children_ids
        self.assertEqual(len(db_builds), 40)
        b133_master = db_builds[-5:]
        self.assertEqual(
            b133_master.mapped('params_id.upgrade_to_build_id.params_id.version_id.name'),
            ['master']
        )
        self.assertEqual(
            b133_master.mapped('params_id.upgrade_from_build_id.params_id.version_id.name'),
            ['saas-13.3']
        )
        self.assertEqual(
            [b.params_id.dump_db.db_suffix for b in b133_master],
            ['account', 'l10n_be', 'l10n_ch', 'mail', 'stock'] # is this order ok?
        )
        import pprint
        pprint.pprint(b133_master.params_id.read())

    def test_step_upgrade(self):
        def docker_run(cmd, log_path, *args, **kwargs):
            pass