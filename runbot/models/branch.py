# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class Branch(models.Model):
    _name = "runbot.branch"
    _description = "Branch"
    _order = 'name'
    _sql_constraints = [('branch_repo_uniq', 'unique (name,remote_id)', 'The branch must be unique per repository !')]

    name = fields.Char('Ref Name', required=True)
    remote_id = fields.Many2one('runbot.remote', 'Remote', required=True, ondelete='cascade')

    head = fields.Many2one('runbot.commit', 'Head Commit')
    head_name = fields.Char('Head name', related='head.name', store=True)

    reference_name = fields.Char(compute='_compute_reference_name', store=True)
    bundle_id = fields.Many2one('runbot.bundle', 'Bundle', readonly=True, ondelete='cascade')

    is_pr = fields.Boolean('IS a pr', required=True)
    pull_head_name = fields.Char(compute='_compute_branch_infos', string='PR HEAD name', readonly=1, store=True)
    pull_head_remote_id = fields.Many2one('runbot.remote', 'Pull head repository', compute='_compute_branch_infos', ondelete='cascade')
    target_branch_name = fields.Char(compute='_compute_branch_infos', string='PR target branch', store=True)

    branch_url = fields.Char(compute='_compute_branch_url', string='Branch url', readonly=1)
    dname = fields.Char('Display name', compute='_compute_dname')

    # alive = fields.Boolean('Alive', default=True)
    # TODO branch exist or not, pr is open or not. Should replace old _is_on_remote behaviour

    @api.depends('name', 'remote_id.short_name')
    def _compute_dname(self):
        for branch in self:
            branch.dname = '%s:%s' % (branch.remote_id.short_name, branch.name)

    @api.depends('name', 'target_branch_name', 'pull_head_name', 'pull_head_remote_id')
    def _compute_reference_name(self):
        """
        Unique reference for a branch inside a bundle.
            - branch_name for branches
            - branch name part of pull_head_name for pr if remote is known
            - pull_head_name (organisation:branch_name) for external pr
        """
        for branch in self:
            if branch.is_pr:
                _, name = branch.pull_head_name.split(':')
                if branch.pull_head_remote_id:
                    branch.reference_name = name
                else:
                    branch.reference_name = branch.pull_head_name  # repo is not known, not in repo list must be an external pr, so use complete label
            else:
                branch.reference_name = branch.name

    @api.depends('name')
    def _compute_branch_infos(self, pull_info=None):
        """compute branch_url, pull_head_name and target_branch_name based on name"""
        name_to_remote = {}
        for branch in self:
            if branch.name:
                pi = pull_info or branch._get_pull_info()
                if pi:
                    try:
                        branch.target_branch_name = pi['base']['ref']
                        branch.pull_head_name = pi['head']['label']
                        pull_head_repo_name = pi['head']['repo']['full_name']
                        if pull_head_repo_name not in name_to_remote:
                            name_to_remote[pull_head_repo_name] = self.env['runbot.remote'].search([('name', 'like', '%%:%s' % pull_head_repo_name)], limit=1)
                        branch.pull_head_remote_id = name_to_remote[pull_head_repo_name]
                    except TypeError:
                        _logger.exception('Error for pr %s using pull_info %s', branch.name , pi)
                        raise

    @api.depends('name', 'remote_id.base_url', 'is_pr')
    def _compute_branch_url(self):
        """compute the branch url based on name"""
        for branch in self:
            if branch.name:
                if branch.is_pr:
                    branch.branch_url = "https://%s/pull/%s" % (branch.remote_id.base_url, branch.name)
                else:
                    branch.branch_url = "https://%s/tree/%s" % (branch.remote_id.base_url, branch.name)
            else:
                branch.branch_url = ''

    @api.model_create_single
    def create(self, vals):
        branch = super().create(vals)
        branch.bundle_id = self.env['runbot.bundle']._get(branch)
        assert branch.bundle_id
        return branch

    def write(self, values):
        head = self.head
        super().write(values)
        if head != self.head:
            self.env['runbot.ref.log'].create({'commit_id': head.id})

    def _get_pull_info(self):
        self.ensure_one()
        remote = self.remote_id
        if self.is_pr:
            return remote._github('/repos/:owner/:repo/pulls/%s' % self.name, ignore_errors=False) or {} # TODO catch and send a managable exception
        return {}

    def recompute_infos(self):
        """ public method to recompute infos on demand """
        self._compute_branch_infos()


class RefLog(models.Model):
    _name = 'runbot.ref.log'
    _description = 'Ref log'
    _log_access = False

    commit_id = fields.Many2one('runbot.commit', index=True)
    branch_id = fields.Many2one('runbot.branch', index=True)
    date = fields.Datetime(default=fields.Datetime.now)
