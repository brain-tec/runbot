import logging
import re
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class Dockerfile(models.Model):
    _name = 'runbot.dockerfile'
    _description = "Dockerfile"

    name = fields.Char('Dockerfile name', required=True, help="Name of Dockerfile")
    image_tag = fields.Char(compute='_compute_image_tag', store=True)
    template_id = fields.Many2one('ir.ui.view', string='Docker Template', domain=[('type', '=', 'dockerfile')], context={'default_type': 'dockerfile', 'default_arch_base': '<t></t>'})
    arch_base = fields.Text(related='template_id.arch_base', readonly=False)
    dockerfile = fields.Text(compute='_compute_dockerfile')
    is_default = fields.Boolean('Default', help='Default Dockerfile', default=False)
    version_ids = fields.One2many('runbot.version', 'dockerfile_id')

    @api.depends('template_id')
    def _compute_dockerfile(self):
        for rec in self:
            rec.dockerfile = rec.template_id.render() if rec.template_id else ''

    @api.depends('name')
    def _compute_image_tag(self):
        for rec in self:
            if rec.name:
                rec.image_tag = 'odoo:%s' % re.sub(r'[ /:\(\)\[\]]', '', rec.name)

    @api.model
    def get_default(self):
        return self.search([('is_default', '=', 'True')], order='id desc', limit=1)
