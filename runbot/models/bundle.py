import glob
import re
import time

from ..common import s2human, dt2time
from babel.dates import format_timedelta
from datetime import timedelta

from collections import defaultdict

from odoo import models, fields, api

#Todo test: create will invalid branch name, pull request

class Version(models.Model):
    _name = "runbot.version"
    _description = "Version"

    name = fields.Char('Version name')
    number = fields.Char('Comparable version number', compute='_compute_version_number', store=True)
    is_major = fields.Char('Comparable version number', compute='_compute_version_number', store=True)

    @api.depends('name')
    def _compute_version_number(self):
        for version in self:
            if version.name == 'master':
                version.number = '~'
                version.is_major = False
            else:
                # max version number with this format: 99.99
                version.number = '.'.join([elem.zfill(2) for elem in re.sub(r'[^0-9\.]', '', version.name).split('.')])
                version.is_major = all(elem=='00' for elem in version.number.split('.')[1:])


class Project(models.Model):
    _name = 'runbot.project'
    _description = 'Project'

    name = fields.Char('Category name', required=True, unique=True, help="Name of the base branch")
    trigger_ids = fields.One2many('runbot.trigger', 'project_id', string='Triggers', required=True, unique=True, help="Name of the base branch")

class Bundle(models.Model):
    _name = "runbot.bundle"
    _description = "Bundle"

    name = fields.Char('Bundle name', required=True, unique=True, help="Name of the base branch")
    project_id = fields.Many2one('runbot.project')
    sticky = fields.Boolean(store=True)
    version_id = fields.Many2one('runbot.version', 'Version')
    version_number = fields.Char(related='version_id.number', store=True)
    branch_ids = fields.One2many('runbot.branch', 'bundle_id')

    # custom behaviour
    rebuild_requested = fields.Boolean("Request a rebuild", help="Rebuild the latest commit even when no_auto_build is set.", default=False)
    no_build = fields.Boolean('No build')
    modules = fields.Char("Modules to install", help="Comma-separated list of modules to install and test.")

    batch_ids = fields.One2many('runbot.batch', 'bundle_id')
    last_batch = fields.Many2one('runbot.batch', index=True)
    last_batchs = fields.Many2many('runbot.batch', 'Last batchs', compute='_compute_last_batchs')

    is_base = fields.Boolean('Is base')
    base_id = fields.Many2one('runbot.bundle', 'Base bundle', compute='_compute_base_id', store=True)
    defined_base_id = fields.Many2one('runbot.bundle', 'Forced base bundle') # todo add constrains on project
    previous_version_bundle_id = fields.Many2one('runbot.bundle', 'Base bundle', compute='_compute_previous_version_bundle_id', store=True)


    @api.depends('is_base', 'defined_base_id', 'base_id.is_base')
    def _compute_base_id(self):
        bases_by_project = {}
        for bundle in self:
            if bundle.is_base:
                bundle.base_id = bundle
                continue
            if bundle.defined_base_id:
                bundle.base_id = bundle.defined_base_id
                continue
            project_id = bundle.project_id.id
            if project_id in bases_by_project:  # small perf imp for udge bartched
                base_bundles = bases_by_project[project_id]
            else:
                base_bundles = self.search([('is_base', '=', True), ('project_id', '=', project_id)])
                bases_by_project[project_id] = base_bundles
            for candidate in base_bundles:
                if bundle.name.startswith(candidate.name):
                    bundle.base_id = candidate
                    break
                elif bundle.name == 'master':
                    bundle.base_id = candidate

    def _compute_previous_version_bundle_id(self):
        for bundle in self:
            if not bundle.is_base:
                bundle.previous_version_bundle_id = bundle.base_id._compute_previous_version_bundle_id
            else:
                previous_version = self.env['runbot.version'].search([
                    ('number', '<', bundle.version_id.version),
                    ('is_major', '=', True)
                ], order='number desc', limit=1)

    def _init_column(self, column_name):
        if column_name not in ('version_number',):
            return super()._init_column(column_name)

        if column_name == 'version_number':
            import traceback
            traceback.print_stack()
            for version in self.env['runbot.version'].search([]):
                self.search([('version_id', '=', version.id)]).write({'version_number':version.number})


    def _compute_last_batchs(self):
        if self:
            batch_ids = defaultdict(list)
            self.env.cr.execute("""
                SELECT
                    id
                FROM (
                    SELECT
                        batch.id AS id,
                        row_number() OVER (PARTITION BY batch.bundle_id order by batch.id desc) AS row
                    FROM
                        runbot_bundle bundle INNER JOIN runbot_batch batch ON bundle.id=batch.bundle_id
                    WHERE
                        bundle.id in %s
                    ) AS bundle_batch
                WHERE
                    row <= 4
                ORDER BY row, id desc
                """, [tuple(self.ids)]
            )
            batchs = self.env['runbot.batch'].browse([r[0] for r in self.env.cr.fetchall()])
            for batch in batchs:
                batch_ids[batch.bundle_id.id].append(batch.id)

            for bundle in self:
                bundle.last_batchs = [(6, 0, batch_ids[bundle.id])]


    def toggle_request_bundle_rebuild(self):
        for branch in self:
            if not branch.rebuild_requested:
                branch.rebuild_requested = True
                branch.repo_id.sudo().set_hook_time(time.time())
            else:
                branch.rebuild_requested = False

    def write(self, values):
        super().write(values)
        #if 'is_base' in values:
        #    for bundle in self:
        #        self.env['runbot.bundle'].search([('name', '=like', '%s%%' % bundle.name), ('project_id', '=', self.project_id.id)])._compute_base_id()

    def _get(self, name, project):
        project.ensure_one()
        bundle = self.search([('name', '=', name), ('project_id', '=', project.id)])
        if not bundle:
            bundle = self.create({
                'name': name,
                'project_id': project.id,
            })
        return bundle

    def _get_preparing_batch(self):
        # find last bundle batch or create one
        if self.last_batch.state != preparing:
            self.last_batch._skip()
            preparing = self.env['runbot.batch'].create({
                'last_update': fields.Datetime.Now(),
                'bundle_id': self.id,
                'state': 'preparing',
            })
            self.last_batch = preparing
        return self.last_batch

    def _target_changed(self):
        self.add_warning()

    def _last_succes(self):
        # search last bundle where all linked builds are success
        return None


class Batch(models.Model):
    _name = "runbot.batch"
    _description = "Bundle batch"

    last_update = fields.Datetime('Last ref update')
    bundle_id = fields.Many2one('runbot.bundle', required=True, index=True)
    batch_commit_ids = fields.One2many('runbot.batch.commit', 'batch_id')
    slot_ids = fields.One2many('runbot.batch.slot', 'batch_id')
    state = fields.Selection([('preparing', 'Preparing'), ('ready', 'Ready'), ('complete', 'Complete'), ('done', 'Done')])
    hidden = fields.Boolean('Hidden', default=False)
    age = fields.Integer(compute='_compute_age', string='Build age')


    @api.depends('create_date')
    def _compute_age(self):
        """Return the time between job start and now"""
        for batch in self:
            if batch.create_date:
                batch.age = int(1587461700 - dt2time(batch.create_date)) # TODO remove hack time.time()
            else:
                batch.buildage_age = 0

    def get_formated_age(self):
        return format_timedelta(
            timedelta(seconds=-self.age),
            threshold=2.1,
            add_direction=True, locale='en'
        )

    def _new_commit(self, commit, repo):
        # if not the same hash for repo_group:
        self.last_update = fields.Datetime.now()
        for batch_commit in self.batch_commit_ids:
            # case 1: a commit already exists for the repo (pr+branch, or fast push)
            if batch_commit.commit_id.repo_group_id == commit.repo_group_id:
                batch_commit.commit_id = commit
                batch_commit.repo_id = repo
                break
        else:
            self.env['runbot.batch.commit'].create({
                'commit_id': commit.id,
                'batch_id': self.id,
                'match_type': 'head',
                'repo_id': repo.id,
            })

    def _skip(self):
        if not self or self.sticky:
            return
        # foreach pending build, if build is not in another batch, skip.

    def _start(self):
        #  For all commit on real branches:
        self.state = 'ready'
        triggers = self.env['runbot.trigger'].search([('project_id', '=', self.bundle_id.project_id)])
        pushed_repo_groups = self.batch_commit_ids.mapped('repos_group_ids') | self.batch_commit_ids.mapped('dependency_ids')

        #  save commit state for all trigger dependencies and repo
        trigger_repos_groups = triggers.mapped('repo_group_id')
        for missing_repo_group in pushed_repo_groups-trigger_repos_groups:
            break
            # find commit for missing_repo_group in a corresponding branch: branch head in the same bundle, or fallback on base_repo
        for trigger in triggers:
            if trigger.repo_group_ids & pushed_repo_groups:  # there is a new commit in this in this trigger
                break
                # todo create build

    def github_status(self):
        pass

            # todo execute triggers


class BatchCommit(models.Model):
    _name = 'runbot.batch.commit'
    _description = "Bundle batch commit"

    commit_id = fields.Many2one('runbot.commit', index=True)
    repo_id = fields.Many2one('runbot.repo', string='Repo') # discovered in repo
    batch_id = fields.Many2one('runbot.batch', index=True)
    match_type = fields.Selection([('new', 'New head of branch'), ('head', 'Head of branch'), ('default', 'Found on base branch')])  # HEAD, DEFAULT


class BatchSlot(models.Model):
    _name = 'runbot.batch.slot'
    _description = 'Link between a bundle batch and a build'

    _fa_link_type = {'created': 'hashtag', 'matched': 'link', 'rebuild': 'refresh'}

    batch_id = fields.Many2one('runbot.batch')
    trigger_id = fields.Many2one('runbot.trigger', index=True)
    build_id = fields.Many2one('runbot.build', index=True)
    link_type = fields.Selection([('created', 'Build created'), ('matched', 'Existing build matched'), ('rebuild', 'Rebuild')], required=True) # rebuild type?
    active = fields.Boolean('Attached')
    result = fields.Selection("Result", related='build_id.global_result')
    # rebuild, what to do: since build ccan be in multiple batch:
    # - replace for all batch?
    # - only available on batch and replace for batch only?
    # - create a new bundle batch will new linked build?

    def fa_link_type(self):
        return self._fa_link_type.get(self.link_type, 'exclamation-triangle')