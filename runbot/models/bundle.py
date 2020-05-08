import glob
import re
import time
import logging

from ..common import s2human, dt2time
from babel.dates import format_timedelta
from datetime import timedelta

from collections import defaultdict

from odoo import models, fields, api

#Todo test: create will invalid branch name, pull request

_logger = logging.getLogger(__name__)

class Version(models.Model):
    _name = "runbot.version"
    _description = "Version"

    name = fields.Char('Version name')
    number = fields.Char('Comparable version number', compute='_compute_version_number', store=True)
    is_major = fields.Char('Is major version', compute='_compute_version_number', store=True)

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

    def _get(self, name):
        version = self.search([('name', '=', name)])
        if not version:
            version = self.create({
                'name': name,
            })
        return version


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
    branch_ids = fields.One2many('runbot.branch', 'bundle_id')

    # custom behaviour
    rebuild_requested = fields.Boolean("Request a rebuild", help="Rebuild the latest commit even when no_auto_build is set.", default=False)
    no_build = fields.Boolean('No build')
    modules = fields.Char("Modules to install", help="Comma-separated list of modules to install and test.")

    batch_ids = fields.One2many('runbot.batch', 'bundle_id')
    last_batch = fields.Many2one('runbot.batch', index=True)
    last_batchs = fields.Many2many('runbot.batch', 'Last batchs', compute='_compute_last_batchs')

    sticky = fields.Boolean('Sticky')
    is_base = fields.Boolean('Is base')
    defined_base_id = fields.Many2one('runbot.bundle', 'Forced base bundle') # todo add constrains on project
    base_id = fields.Many2one('runbot.bundle', 'Base bundle', compute='_compute_base_id', store=True)

    version_id = fields.Many2one('runbot.version', 'Version', compute='_compute_version_id', store=True)
    version_number = fields.Char(related='version_id.number', store=True)

    previous_version_base_id = fields.Many2one('runbot.bundle', 'Previous base bundle', compute='_compute_previous_version_base_id')
    intermediate_version_base_ids = fields.Many2many('runbot.bundle', 'Intermediate base bundles', compute='_compute_previous_version_base_id')


    @api.depends('name', 'is_base', 'defined_base_id', 'base_id.is_base', 'project_id')
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


    @api.depends('is_base', 'base_id.version_id')
    def _compute_version_id(self):
        for bundle in self.sorted(key='is_base', reverse=True):
            if not bundle.is_base:
                bundle.version_id = bundle.base_id.version_id
                continue
            bundle.version_id = self.env['runbot.version']._get(bundle.name)

    @api.depends('is_base', 'base_id.previous_version_base_id')
    def _compute_previous_version_base_id(self):
        for bundle in self.sorted(key='is_base', reverse=True):

            if not bundle.is_base:
                bundle.previous_version_base_id = bundle.base_id.previous_version_base_id
                bundle.intermediate_version_base_ids = bundle.base_id.intermediate_version_base_ids
            else:
                previous_version = self.env['runbot.version'].search([
                    ('number', '<', bundle.version_id.number),
                    ('is_major', '=', True)
                ], order='number desc', limit=1)
                if previous_version:
                    # todo what if multiple results
                    bundle.previous_version_base_id = self.env['runbot.bundle'].search([
                        ('version_id', '=', previous_version.id),
                        ('is_base', '=', True),
                        ('project_id', '=', bundle.project_id.id)
                    ])
                    bundle.intermediate_version_base_ids = self.env['runbot.bundle'].search([
                        ('version_number', '>', previous_version.number),
                        ('version_number', '<', bundle.version_id.number),
                        ('is_base', '=', True),
                        ('project_id', '=', bundle.project_id.id)
                    ])

                else:
                    bundle.previous_version_base_id = False
                    bundle.intermediate_version_base_ids = False

    #def _init_column(self, column_name):
    #    if column_name not in ('version_number',):
    #        return super()._init_column(column_name)
#
    #    if column_name == 'version_number':
    #        import traceback
    #        traceback.print_stack()
    #        for version in self.env['runbot.version'].search([]):
    #            self.search([('version_id', '=', version.id)]).write({'version_number':version.number})


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

    def toggle_request_bundle_rebuild(self): # TODO check
        for branch in self:
            if not branch.rebuild_requested:
                branch.rebuild_requested = True
                branch.repo_id.sudo().set_hook_time(time.time())
            else:
                branch.rebuild_requested = False

    def create(self, values_list):
        self.flush()
        return super().create(values_list)
        #if any(values.get('is_base') for values in values_list):
        #    (self.search([
        #        ('project_id', 'in', self.mapped('project_id').ids),
        #        ('is_base', '=', True)
        #    ]) + self)._compute_previous_version_base_id()

    def write(self, values):
        super().write(values)
        self.flush()
        #if 'is_base' in values:
        #    (self.search([
        #        ('project_id', 'in', self.mapped('project_id').ids),
        #        ('is_base', '=', True)
        #    ]) + self)._compute_previous_version_base_id()
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
        if self.last_batch.state != 'preparing':
            self.last_batch._skip()
            preparing = self.env['runbot.batch'].create({
                'last_update': fields.Datetime.now(),
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

    def _new_commit(self, commit):
        # if not the same hash for repo_group:
        self.last_update = fields.Datetime.now()
        for batch_commit in self.batch_commit_ids:
            # case 1: a commit already exists for the repo (pr+branch, or fast push)
            if batch_commit.commit_id.repo_id == commit.repo_id:
                batch_commit.commit_id = commit
                break
        else:
            self.env['runbot.batch.commit'].create({
                'commit_id': commit.id,
                'batch_id': self.id,
                'match_type': 'new',
            })

    def _skip(self):
        if not self or self.bundle_id.is_base:
            return
        # TODO
        # foreach pending build, if build is not in another batch, skip.

    def _start(self):
        #  For all commit on real branches:
        self.state = 'ready'
        triggers = self.env['runbot.trigger'].search([('project_id', '=', self.bundle_id.project_id)])
        pushed_repo = self.batch_commit_ids.mapped('repo_id') # TODO check match_type?

        #  save commit state for all trigger dependencies and repo
        trigger_repos = triggers.mapped('repo_id')
        for missing_repo in pushed_repo-trigger_repos:
            break
            # find commit for missing_repo_group in a corresponding branch: branch head in the same bundle, or fallback on base_repo
        for trigger in triggers:
            if trigger.repo_ids & pushed_repo:  # there is a new commit in this in this trigger
                break
                # todo create build

    def github_status(self):
        pass

            # todo execute triggers


    def _github_status_notify_all(self, status):
        """Notify each repo with a status"""
        self.ensure_one()
        if self.config_id.update_github_state:
            remote_ids = {b.repo_id.id for b in self.search([('name', '=', self.name)])}
            build_name = self.name # todo adapt: custom ci per trigger
            user_id = self.env.user.id
            _dbname = self.env.cr.dbname
            _context = self.env.context
            build_id = self.id
            def send_github_status():
                try:
                    db_registry = registry(_dbname)
                    with api.Environment.manage(), db_registry.cursor() as cr:
                        env = api.Environment(cr, user_id, _context)
                        remotes = env['runbot.repo'].browse(remote_ids)
                        for remote in remotes:
                            _logger.debug(
                                "github updating %s status %s to %s in repo %s",
                                status['context'], build_name, status['state'], remote.name)
                            repo._github('/repos/:owner/:repo/statuses/%s' % build_name, status, ignore_errors=True)
                except:
                    _logger.exception('Something went wrong sending notification for %s', build_id)
            self._cr.after('commit', send_github_status)


    # TODO should be on bundle: multiple build for the same hash
    def _github_status(self):
        """Notify github of failed/successful builds"""
        for build in self:
            if build.parent_id:
                if build.orphan_result:
                    _logger.debug('Skipping result for orphan build %s', self.id)
                else:
                    build.parent_id._github_status()
            elif build.config_id.update_github_state:
                runbot_domain = self.env['runbot.repo']._domain()
                desc = "runbot build %s" % (build.dest,)

                if build.global_result in ('ko', 'warn'):
                    state = 'failure'
                elif build.global_state == 'testing':
                    state = 'pending'
                elif build.global_state in ('running', 'done'):
                    state = 'error'
                    if build.global_result == 'ok':
                        state = 'success'
                else:
                    _logger.debug("skipping github status for build %s ", build.id)
                    continue
                desc += " (runtime %ss)" % (build.job_time,)

                status = {
                    "state": state,
                    "target_url": "http://%s/runbot/build/%s" % (runbot_domain, build.id),
                    "description": desc,
                    "context": "ci/runbot"
                }
                if self.last_github_state != state:
                    build._github_status_notify_all(status)
                    self.last_github_state = state
                else:
                    _logger.debug('Skipping unchanged status for %s', self.id)


class BatchCommit(models.Model):
    _name = 'runbot.batch.commit'
    _description = "Bundle batch commit"

    commit_id = fields.Many2one('runbot.commit', index=True)
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