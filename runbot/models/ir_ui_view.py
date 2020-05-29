


from ..common import s2human, s2human_long, dt2time

from odoo import models

class IrUiView(models.Model):
    _inherit = ["ir.ui.view"]

    def _prepare_qcontext(self):
        qcontext = super(IrUiView, self)._prepare_qcontext()
        qcontext['s2human'] = s2human
        qcontext['s2human_long'] = s2human_long
        return qcontext
