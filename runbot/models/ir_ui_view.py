from ..common import s2human, s2human_long
from odoo import models, fields
from odoo.http import request


class IrUiView(models.Model):
    _inherit = ["ir.ui.view"]

    type = fields.Selection(selection_add=[('dockerfile', 'Dockerfile')])

    def _prepare_qcontext(self):
        qcontext = super(IrUiView, self)._prepare_qcontext()

        if request and getattr(request, 'is_frontend', False):
            qcontext['s2human'] = s2human
            qcontext['s2human_long'] = s2human_long
        return qcontext

    def postprocess_and_fields(self, model, node, view_id):
        if self.type == 'dockerfile':
            return super().postprocess_and_fields('ir.ui.view', node, view_id)
        return super().postprocess_and_fields(model, node, view_id)
