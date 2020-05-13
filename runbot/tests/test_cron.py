# -*- coding: utf-8 -*-
from unittest.mock import patch
from odoo.tests import common
from .common import RunbotCase


class TestCron(RunbotCase):

    def setUp(self):
        super(TestCron, self).setUp()
        self.start_patcher('_get_cron_period', 'odoo.addons.runbot.models.runbot.Runbot._get_cron_period', 2)

    @patch('odoo.addons.runbot.models.repo.Repo._update_batches')
    def test_cron_schedule(self, mock_update_batches):
        """ test that cron_fetch_and_schedule do its work """
        self.env['ir.config_parameter'].sudo().set_param('runbot.runbot_update_frequency', 1)
        self.env['ir.config_parameter'].sudo().set_param('runbot.runbot_do_fetch', True)
        # TODO fix this, repo disabled
        self.env['runbot.repo'].search([('id', '!=', self.repo_server.id)]).write({'mode': 'disabled'}) #  disable all other existing repo than repo_server
        self.Runbot._cron()
        mock_update_batches.assert_called()

    @patch('odoo.addons.runbot.models.host.RunbotHost._docker_build')
    @patch('odoo.addons.runbot.models.host.RunbotHost._bootstrap')
    @patch('odoo.addons.runbot.models.runbot.Runbot._scheduler')
    def test_cron_build(self, mock_scheduler, mock_host_bootstrap, mock_host_docker_build):
        """ test that cron_fetch_and_build do its work """
        hostname = 'cronhost.runbot.com'
        self.patchers['fqdn_patcher'].return_value = hostname
        self.env['ir.config_parameter'].sudo().set_param('runbot.runbot_update_frequency', 1)
        self.env['ir.config_parameter'].sudo().set_param('runbot.runbot_do_schedule', True)
        self.env['runbot.repo'].search([('id', '!=', self.repo_server.id)]).write({'mode': 'disabled'}) #  disable all other existing repo than repo_server
        self.Runbot._cron()
        mock_scheduler.assert_called()
        mock_host_bootstrap.assert_called()
        mock_host_docker_build.assert_called()
        host = self.env['runbot.host'].search([('name', '=', hostname)])
        self.assertTrue(host, 'A new host should have been created')
        self.assertGreater(host.psql_conn_count, 0, 'A least one connection should exist on the current psql batch')
