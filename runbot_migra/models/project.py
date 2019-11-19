# -*- coding: utf-8 -*-

import logging
from odoo import models, fields, api


_logger = logging.getLogger(__name__)


class Project(models.Model):

    _name = "runbot_migra.project"
    _description = "Migration project"

    name = fields.Char('Name', required=True)
    server_repo = fields.Many2one('runbot_migra.repo', 'Odoo server repo', required=True)
    addons_repo_ids = fields.Many2many('runbot_migra.repo', string='Additional addons repos')
    migration_scripts_repo = fields.Many2one('runbot_migra.repo', 'Migration scripts repo', required=True)
    version_target = fields.Char('Targeted version', help='Final version, used by the update instance')
    versions = fields.Char('Start versions', help='Comma separated intermediary versions')
