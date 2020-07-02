import logging
import re
from odoo import models, fields, api, tools


_logger = logging.getLogger(__name__)

class Version(models.Model):
    _name = 'runbot.version'
    _description = "Version"
    _order = 'number desc,id'

    name = fields.Char('Version name')
    number = fields.Char('Version number', compute='_compute_version_number', store=True, help="Usefull to sort by version")
    is_major = fields.Char('Is major version', compute='_compute_version_number', store=True)

    base_bundle_id = fields.Many2one('runbot.bundle', compute='_compute_base_bundle_id')

    previous_major_version_id = fields.Many2one('runbot.version', compute='_compute_version_relations')
    intermediate_version_ids = fields.Many2many('runbot.version', compute='_compute_version_relations')
    next_major_version_id = fields.Many2one('runbot.version', compute='_compute_version_relations')
    next_intermediate_version_ids = fields.Many2many('runbot.version', compute='_compute_version_relations')

    @api.depends('name')
    def _compute_version_number(self):
        for version in self:
            if version.name == 'master':
                version.number = '~'
                version.is_major = False
            else:
                # max version number with this format: 99.99
                version.number = '.'.join([elem.zfill(2) for elem in re.sub(r'[^0-9\.]', '', version.name).split('.')])
                version.is_major = all(elem == '00' for elem in version.number.split('.')[1:])


    def create(self, values):
        model = self.browse()
        model._get_id.clear_cache(model)  # TODO test me
        return super().create(values)


    def _get(self, name):
        return self.browse(self._get_id(name))

    @tools.ormcache('name')
    def _get_id(self, name):
        version = self.search([('name', '=', name)])
        if not version:
            version = self.create({
                'name': name,
            })
        return version.id

    @api.depends('is_major', 'number')
    def _compute_version_relations(self):
        all_versions = self.search([], order='number')
        for version in self:
            version.previous_major_version_id = next(
                (
                    v
                    for v in reversed(all_versions)
                    if v.is_major and v.number < version.number
                ), self.browse())
            if version.previous_major_version_id:
                version.intermediate_version_ids = all_versions.filtered(
                    lambda v, current=version: v.number > current.previous_major_version_id.number and v.number < current.number
                    )
            else:
                version.intermediate_version_ids = all_versions.filtered(
                    lambda v, current=version: v.number < current.number
                    )
            version.next_major_version_id = next(
                (
                    v
                    for v in all_versions
                    if (v.is_major or v.name == 'master') and v.number > version.number
                ), self.browse())
            if version.next_major_version_id:
                version.next_intermediate_version_ids = all_versions.filtered(
                    lambda v, current=version: v.number < current.next_major_version_id.number and v.number > current.number
                    )
            else:
                version.next_intermediate_version_ids = all_versions.filtered(
                    lambda v, current=version: v.number > current.number
                    )

    #@api.depends('base_bundle_id.is_base', 'base_bundle_id.version_id', 'base_bundle_id.project_id')
    @api.depends_context('project_id')
    def _compute_base_bundle_id(self):
        project_id = self.env.context.get('project_id')
        if not project_id:
            _logger.warning("_compute_base_bundle_id: no project_id in context")
            project_id = self.env.ref('runbot.main_project').id

        bundles = self.env['runbot.bundle'].search([
            ('version_id', 'in', self.ids),
            ('is_base', '=', True),
            ('project_id', '=', project_id)
        ])
        bundle_by_version = {bundle.version_id.id:bundle for bundle in bundles}
        for version in self:
            version.base_bundle_id = bundle_by_version.get(version.id)
