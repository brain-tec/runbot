import logging
from odoo import models, fields
_logger = logging.getLogger(__name__)


class RunbotDatabase(models.Model):
    _name = "runbot.database"
    _description = "Database"

    name = fields.Char('Host name', required=True, unique=True)
    build_id = fields.Many2one('runbot.build', required=True)
