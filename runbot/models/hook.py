import datetime
import logging

from odoo import models, fields, api

from ..fields import JsonDictField


_logger = logging.getLogger(__name__)


class Hook(models.Model):
    _name = 'runbot.hook'
    _description = "Queue of received hooks"
    _order = 'id desc, create_date desc'

    payload = JsonDictField(help="Json content received from github")
    remote_id = fields.Many2one('runbot.remote')
    github_event = fields.Char('Github Event')
    active = fields.Boolean('Active', default=True)

    @api.model
    def _gc_hooks(self):
        limit_date = fields.datetime.now() - datetime.timedelta(days=1)
        self.env['runbot.hook'].search([('create_date', '<', limit_date)]).unlink()
        return True

    def _process(self):
        self.ensure_one()
        payload = self.payload
        #force update of dependencies too in case a hook is lost
        if not payload or self.github_event == 'push':
            self.remote_id.repo_id.hooked = True
        elif self.github_event == 'pull_request':
            pr_number = payload.get('pull_request', {}).get('number', '')
            branch = self.env['runbot.branch'].sudo().search([('remote_id', '=', self.remote_id.id), ('name', '=', pr_number)])
            branch.recompute_infos(payload.get('pull_request', {}))
            if payload.get('action') in ('synchronize', 'opened', 'reopened'):
                self.remote_id.repo_id.hooked = True
        #    remaining recurrent actions: labeled, review_requested, review_request_removed
        elif self.github_event == 'delete':
            if payload.get('ref_type') == 'branch':
                branch_ref = payload.get('ref')
                _logger.info('Branch %s in repo %s was deleted', branch_ref, self.remote_id.repo_id.name)
                branch = self.env['runbot.branch'].sudo().search([('remote_id', '=', self.remote_id.id), ('name', '=', branch_ref)])
                branch.alive = False
        self.active = False
