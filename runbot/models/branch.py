# -*- coding: utf-8 -*-
import logging
import re
import time
from subprocess import CalledProcessError
from odoo import models, fields, api
from odoo.osv import expression

_logger = logging.getLogger(__name__)


class Branch(models.Model):
    _name = "runbot.branch"
    _description = "Branch"
    _order = 'name'
    _sql_constraints = [('branch_repo_uniq', 'unique (name,repo_id)', 'The branch must be unique per repository !')]

    head = fields.Many2one('runbot.commit', 'Head Commit')
    head_name = fields.Char('Head name', related='head.name', store=True)
    bundle_id = fields.Many2one('runbot.bundle', 'Bundle', readonly=True, ondelete='cascade')
    remote_id = fields.Many2one('runbot.remote', 'Remote', required=True, ondelete='cascade')
    name = fields.Char('Ref Name', required=True)
    branch_name = fields.Char(compute='_get_branch_infos', string='Branch', readonly=1, store=True)
    reference_name = fields.Char(compute='_compute_reference_name', store=True)
    branch_url = fields.Char(compute='_get_branch_url', string='Branch url', readonly=1)
    pull_head_name = fields.Char(compute='_get_branch_infos', string='PR HEAD name', readonly=1, store=True)
    pull_head_remote_id = fields.Many2one('runbot.remote', 'Pull head repository', ondelete='cascade')
    target_branch_name = fields.Char(compute='_get_branch_infos', string='PR target branch', store=True)
    pull_branch_name = fields.Char(compute='_compute_pull_branch_name', string='Branch display name')
    sticky = fields.Boolean('Sticky')
    # TODO remove sticky and main and stuff.
    # should be based on a bundle, but need a project to know corresponding bundle
    # anyway, display will be by project (?)

    #closest_sticky = fields.Many2one('runbot.branch', compute='_compute_closest_sticky', string='Closest sticky')
    #defined_sticky = fields.Many2one('runbot.branch', string='Force sticky')
    #previous_version = fields.Many2one('runbot.branch', compute='_compute_previous_version', string='Previous version branch')
    #intermediate_stickies = fields.Many2many('runbot.branch', compute='_compute_intermediate_stickies', string='Intermediates stickies')
    coverage_result = fields.Float(compute='_compute_coverage_result', type='Float', string='Last coverage', store=False)  # non optimal search in loop, could we store this result ? or optimise
    state = fields.Char('Status')
    priority = fields.Boolean('Build priority', default=False)
    no_auto_build = fields.Boolean("Don't automatically build commit on this branch", default=False)
    make_stats = fields.Boolean('Extract stats from logs', compute='_compute_make_stats', store=True)
    dname = fields.Char('Display name', compute='_compute_dname')
    is_pr = fields.Bollean('IS a pr', required=True)

    @api.depends('branch_name', 'remote_id.short_name')
    def _compute_dname(self):
        for branch in self:
            branch.dname = '%s:%s' % (branch.remote_id.short_name, branch.branch_name)

    # todo ass shortcut to create pr in interface as inherited view Create Fast PR
    @api.depends('target_branch_name', 'pull_head_name', 'pull_head_remote_id')
    def _compute_reference_name(self):
        """
        a unique reference for a branch inside a bundle.
            -branch_name for branches
            - branch name part of pull_head_name for pr if repo is known
            - pull_head_name (organisation:branch_name) for external pr
        """
        for branch in self:
            if branch.target_branch_name and branch.pull_head_name:  # odoo:master-remove-duplicate-idx, owner:xxx, 
                _, name = branch.pull_head_name.split(':')  # TODO fix where pullheadname doesnt have : -> old branch, redo get_pull_info
                if branch.pull_head_remote_id:
                    branch.reference_name = name
                else:
                    branch.reference_name = branch.pull_head_name  # repo is not known, not in repo list must be an external pr, so use complete label
            else:
                branch.reference_name = branch.branch_name
        # cases to test:
        # organisation:patch-x (no pull_head_name, should be changed)
        # odoo-dev:master-my-dev
        # odoo-dev:dummy-my-dev -> warning
        # odoo:master-my-dev
        # odoo:master-my-dev
        # odoo:master-my-dev + odoo-dev:master-my-dev
        # -> convention in odoo, this is an error. A branch_name should be unique
        # pr targetting odoo-dev
        #
        # a pr pull head name should be in a repo or one of its forks, we need to check that


    #@api.depends('sticky', 'defined_sticky', 'target_branch_name', 'name')
    ## won't be recompute if a new branch is marked as sticky or sticky is removed, but should be ok if not stored
    #def _compute_closest_sticky(self):
    #    for branch in self:
    #        if branch.sticky:
    #            branch.closest_sticky = branch
    #        elif branch.defined_sticky:
    #            branch.closest_sticky = branch.defined_sticky # be carefull with loop
    #        elif branch.target_branch_name:
    #            corresponding_branch = self.search([('branch_name', '=', branch.target_branch_name), ('repo_id', '=', branch.repo_id.id)])
    #            branch.closest_sticky = corresponding_branch.closest_sticky
    #        else:
    #            repo_ids = (branch.repo_id | branch.repo_id.duplicate_id).ids
    #            self.env.cr.execute("select id from runbot_branch where sticky = 't' and repo_id = any(%s) and %s like name||'%%'", (repo_ids, branch.name or ''))
    #            branch.closest_sticky = self.browse(self.env.cr.fetchone())
#
    #@api.depends('closest_sticky') #, 'closest_sticky.previous_version')
    #def _compute_previous_version(self):
    #    for branch in self.sorted(key='sticky', reverse=True):
    #        # orm does not support non_searchable.non_stored dependency.
    #        # thus, the closest_sticky.previous_version dependency will log an error
    #        # when previous_version is written.
    #        # this dependency is usefull to make the compute recursive, avoiding to have 
    #        # both record and record.closest_sticky in self, in that order, making the record.previous_version
    #        # empty in all cases.
    #        # Sorting self on sticky will mitigate the problem. but it is still posible to
    #        # have computation errors if defined_sticky is not sticky. (which is not a normal use case)
    #        if branch.closest_sticky == branch:
    #            repo_ids = (branch.repo_id | branch.repo_id.duplicate_id).ids
    #            domain = [('branch_name', 'like', '%.0'), ('sticky', '=', True), ('branch_name', '!=', 'master'), ('repo_id', 'in', repo_ids)]
    #            if branch.branch_name != 'master' and branch.id:
    #                domain += [('id', '<', branch.id)]
    #            branch.previous_version = self.search(domain, limit=1, order='id desc')
    #        else:
    #            branch.previous_version = branch.closest_sticky.previous_version
#
    #@api.depends('previous_version', 'closest_sticky')
    #def _compute_intermediate_stickies(self):
    #    for branch in self.sorted(key='sticky', reverse=True):
    #        if branch.closest_sticky == branch:
    #            if not branch.previous_version:
    #                branch.intermediate_stickies = [(5, 0, 0)]
    #                continue
    #            repo_ids = (branch.repo_id | branch.repo_id.duplicate_id).ids
    #            domain = [('id', '>', branch.previous_version.id), ('sticky', '=', True), ('branch_name', '!=', 'master'), ('repo_id', 'in', repo_ids)]
    #            if branch.closest_sticky.branch_name != 'master' and branch.closest_sticky.id:
    #                domain += [('id', '<', branch.closest_sticky.id)]
    #            branch.intermediate_stickies = [(6, 0, self.search(domain, order='id desc').ids)]
    #        else:
    #            branch.intermediate_stickies = [(6, 0, branch.closest_sticky.intermediate_stickies.ids)]

    def _inverse_config_id(self):
        for branch in self:
            branch.branch_config_id = branch.config_id

    def _compute_pull_branch_name(self):
        for branch in self:
            branch.pull_branch_name = branch.pull_head_name.split(':')[-1] if branch.pull_head_name else branch.branch_name

    @api.depends('sticky')
    def _compute_make_stats(self):
        for branch in self:
            branch.make_stats = branch.sticky

    @api.depends('name')
    def _get_branch_infos(self, pull_info=None):
        """compute branch_name, branch_url, pull_head_name and target_branch_name based on name"""
        for branch in self:
            if branch.name:
                branch.branch_name = branch.name.split('/')[-1]
                pi = pull_info or branch._get_pull_info()
                if pi:
                    branch.target_branch_name = pi['base']['ref']
                    branch.pull_head_name = pi['head']['label']
                    pull_head_repo_name = pi['head']['repo']['full_name']
                    branch.pull_head_remote_id = self.env['runbot.remote'].search([('name', 'like', '%%:%s' % pull_head_repo_name)], limit=1)

            else:
                branch.branch_name = ''

    def recompute_infos(self):
        """ public method to recompute infos on demand """
        self._get_branch_infos()

    @api.depends('branch_name')
    def _get_branch_url(self):
        """compute the branch url based on branch_name"""
        for branch in self:
            if branch.name:
                if branch.is_pr:
                    branch.branch_url = "https://%s/pull/%s" % (branch.remote_id.base, branch.branch_name)
                else:
                    branch.branch_url = "https://%s/tree/%s" % (branch.remote_id.base, branch.branch_name)
            else:
                branch.branch_url = ''

    def _get_pull_info(self):
        self.ensure_one()
        remote = self.remote_id
        if remote.token and self.is_pr:
            return remote._github('/repos/:owner/:repo/pulls/%s' % self.name, ignore_errors=True) or {}
        return {}

    def _is_on_remote(self):
        # check that a branch still exists on remote
        self.ensure_one()
        branch = self
        remote = branch.remote_id
        try:
            remote.repo_id._git(['ls-remote', '-q', '--exit-code', remote.name, branch.name])
        except CalledProcessError:
            return False
        return True

    @api.model_create_single
    def create(self, vals):
        #if not vals.get('config_id') and ('use-coverage' in (vals.get('name') or '')):
        #    coverage_config = self.env.ref('runbot.runbot_build_config_test_coverage', raise_if_not_found=False)
        #    if coverage_config:
        #        vals['config_id'] = coverage_config
        branch = super().create(vals)
        branch.bundle_id = self.env['runbot.bundle']._get(branch.reference_name, branch.remote_id.repo_id.project_id)
        assert branch.bundle_id
        return branch
        #note: bundle is created after branch because we need reference_name. Use new? Compute reference another way? or keep bundle_id not required?

    def _get_last_coverage_build(self):
        """ Return the last build with a coverage value > 0"""
        self.ensure_one()
        return self.env['runbot.build'].search([
            ('branch_id.id', '=', self.id),
            ('local_state', 'in', ['done', 'running']),
            ('coverage_result', '>=', 0.0),
        ], order='sequence desc', limit=1)

    def _compute_coverage_result(self):
        """ Compute the coverage result of the last build in branch """
        for branch in self:
            last_build = branch._get_last_coverage_build()
            branch.coverage_result = last_build.coverage_result or 0.0

    # TODO check get_closest_branch corner cases
    # TODO branch alive field
