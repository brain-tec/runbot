import logging
import re
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class DockerStep(models.Model):
    _name = 'runbot.dockerfile.step'
    _description = 'Dockerfile step'

    name = fields.Char('Dockerfile step name', required=True, help="Step description")
    content = fields.Text('Step content', required=True)
    docker_step_order_ids = fields.One2many('runbot.dockerfile.step.order', 'docker_step_id')


class DockerStepOrder(models.Model):
    _name = 'runbot.dockerfile.step.order'
    _description = 'Dockerfile step order'
    _order = 'sequence, id'

    sequence = fields.Integer('Sequence', required=True)
    dockerfile_id = fields.Many2one('runbot.dockerfile', 'Dockerfile', required=True, ondelete='cascade')
    docker_step_id = fields.Many2one('runbot.dockerfile.step', 'Docker Step', required=True, ondelete='cascade')

    @api.onchange('docker_step_id')
    def _onchange_step_id(self):
        self.sequence = 10

    @api.model_create_single
    def create(self, values):
        if 'sequence' not in values and values.get('docker_step_id'):
            values['sequence'] = 10
        if self.pool._init:  # do not duplicate entry on install
            existing = self.search([('sequence', '=', values.get('sequence')), ('dockerfile_id', '=', values.get('dockerfile_id')), ('docker_step_id', '=', values.get('docker_step_id'))])
            if existing:
                return
        return super().create(values)


class Dockerfile(models.Model):
    _name = 'runbot.dockerfile'
    _description = "Dockerfile"

    name = fields.Char('Dockerfile name', required=True, help="Name of Dockerfile")
    image_tag = fields.Char(compute='_compute_image_tag')
    base_from = fields.Char('Dockerfile from', required=True, help="Base FROM")
    docker_step_order_ids = fields.One2many('runbot.dockerfile.step.order', 'dockerfile_id')
    dockerfile = fields.Text(compute='_compute_dockerfile')
    is_default = fields.Boolean('Use as fallback', default=False)
    version_ids = fields.One2many('runbot.version', 'dockerfile_id')

    @api.depends('docker_step_order_ids', 'base_from')
    def _compute_dockerfile(self):
        for rec in self:
            dockerfile = 'FROM %s\n' % rec.base_from
            dockerfile += '\n'.join([step_order.docker_step_id.content for step_order in rec.docker_step_order_ids])
            rec.dockerfile = '%s\n' % dockerfile

    @api.depends('name')
    def _compute_image_tag(self):
        for rec in self:
            rec.image_tag = re.sub(' |/|:', '_', rec.name)
