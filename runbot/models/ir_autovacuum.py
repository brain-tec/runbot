from odoo import api, models


class AutoVacuum(models.AbstractModel):
    _inherit = 'ir.autovacuum'

    @api.model
    def power_on(self, *args, **kwargs):
        self.env['runbot.hook']._gc_hooks()
        return super(AutoVacuum, self).power_on(*args, **kwargs)
