from odoo import models, fields


class Project(models.Model):
    _name = 'runbot.project'
    _description = 'Project'

    name = fields.Char('Project name', required=True)
    group_ids = fields.Many2many('res.groups', string='Required groups')
    keep_sticky_running = fields.Boolean('Keep last sticky builds running')
    trigger_ids = fields.One2many('runbot.trigger', 'project_id', string='Triggers')
    dockerfile_id = fields.Many2one('runbot.dockerfile', index=True, help="Project Default Dockerfile")
    repo_ids = fields.One2many('runbot.repo', 'project_id', string='Repos')

    def _get_description(self):
        return[
            {
                'id': r.id,
                'url': f'{r.get_base_url()}/runbot/json/projects/{r.id}',
                'name': r.name,
                'keep_sticky_running': r.keep_sticky_running,
                'bundles_url': f'{r.get_base_url()}/runbot/json/projects/{r.id}/bundles'
            }
            for r in self
        ]

class Category(models.Model):
    _name = 'runbot.category'
    _description = 'Trigger category'

    name = fields.Char("Name")
    icon = fields.Char("Font awesome icon")
    view_id = fields.Many2one('ir.ui.view', "Link template")
