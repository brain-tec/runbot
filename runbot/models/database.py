import logging
from odoo import models, fields, api
_logger = logging.getLogger(__name__)


class Database(models.Model):
    _name = 'runbot.database'
    _description = "Database"

    name = fields.Char('Host name', required=True)
    build_id = fields.Many2one('runbot.build', index=True, required=True)
    db_suffix = fields.Char(compute='_compute_db_suffix')

    def _compute_db_suffix(self):
        for record in self:
            record.db_suffix = record.name.replace('%s-' % record.build_id.dest, '')

    def create(self, vals_list):
        existing = self.browse()
        to_create = []
        for values in vals_list:
            res = self.search([('name', '=', values['name']), ('build_id', '=', values['build_id'])])
            if res:
                existing |= res
            else:
                to_create.append(values)
        return super().create(to_create) | existing
