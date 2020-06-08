# -*- coding: utf-8 -*-
from unittest.mock import patch
from werkzeug.urls import url_parse

from odoo.tests.common import HttpCase, new_test_user
from odoo.tools import mute_logger


class TestCommitStatus(HttpCase):

    def setUp(self):
        super(TestCommitStatus, self).setUp()
        self.project = self.env['runbot.project'].create({'name': 'Tests'})
        self.repo_server = self.env['runbot.repo'].create({
            'name': 'server',
            'project_id': self.project.id,
            'server_files': 'server.py',
            'addons_paths': 'addons,core/addons'
        })

        self.server_commit = self.env['runbot.commit'].create({
            'name': 'dfdfcfcf0000ffffffffffffffffffffffffffff',
            'repo_id': self.repo_server.id
        })

        self.simple_user = new_test_user(self.env, login='simple', name='simple', password='simple', context={'no_reset_password': True})
        self.runbot_admin = new_test_user(self.env, groups='runbot.group_runbot_admin,base.group_user', login='runbot_admin', name='runbot_admin', password='admin', context={'no_reset_password': True})

    def test_commit_status_resend(self):
        """test commit status resend"""

        commit_status = self.env['runbot.commit.status'].create({
            'commit_id': self.server_commit.id,
            'context': 'ci/test',
            'state': 'failure',
            'target_url': 'https://www.somewhere.com',
            'description': 'test status',
            'create_date': '2020-01-01 15:00:00'
        })

        # 1. test that unauthenticated users are redirected to the login page
        response = self.url_open('/runbot/commit/resend/%s' % commit_status.id)
        parsed_response = url_parse(response.url)
        self.assertIn('redirect=', parsed_response.query)
        self.assertEqual(parsed_response.path, '/web/login')

        # 2. test that a simple Odoo user cannot resend a status
        self.authenticate('simple', 'simple')
        with mute_logger('odoo.addons.http_routing.models.ir_http'):
            response = self.url_open('/runbot/commit/resend/%s' % commit_status.id)
        self.assertEqual(response.status_code, 403)

        # 3. test that a non-existsing commit_status returns a 404
        # 3.1 find a non existing commit status id
        non_existing_id = self.env['runbot.commit.status'].browse(50000).exists() or 50000
        while self.env['runbot.commit.status'].browse(non_existing_id).exists():
            non_existing_id += 1

        self.authenticate('runbot_admin', 'admin')
        response = self.url_open('/runbot/commit/resend/%s' % non_existing_id)
        self.assertEqual(response.status_code, 404)

        # 4. Finally test that a new status is created on resend and that the _send method is called
        with patch('odoo.addons.runbot.models.commit.CommitStatus._send') as send_patcher:
            response = self.url_open('/runbot/commit/resend/%s' % commit_status.id)
            self.assertEqual(response.status_code, 200)
            send_patcher.assert_called()

        last_commit_status = self.env['runbot.commit.status'].search([], order='id desc', limit=1)
        self.assertEqual(last_commit_status.description, 'Status resent by runbot_admin')

        # 5. try to immediately resend the commit should fail to avoid spamming github
        with patch('odoo.addons.runbot.models.commit.CommitStatus._send') as send_patcher:
            response = self.url_open('/runbot/commit/resend/%s' % commit_status.id)
            self.assertEqual(response.status_code, 200)
            send_patcher.assert_not_called()
