import re
import time
import logging
import datetime
import subprocess

from collections import defaultdict
from babel.dates import format_timedelta
from odoo import models, fields, api
from ..common import dt2time, s2human_long


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

    name = fields.Char('Bundle name', required=True, help="Name of the base branch")
    project_id = fields.Many2one('runbot.project', required=True)
    branch_ids = fields.One2many('runbot.branch', 'bundle_id')

    # custom behaviour
    no_build = fields.Boolean('No build')
    build_all = fields.Boolean('Force all triggers')
    modules = fields.Char("Modules to install", help="Comma-separated list of modules to install and test.")

    batch_ids = fields.One2many('runbot.batch', 'bundle_id')
    last_batch = fields.Many2one('runbot.batch', index=True, domain=lambda self: [('category_id', '=', self.env.ref('runbot.default_category').id)])
    last_batchs = fields.Many2many('runbot.batch', 'Last batchs', compute='_compute_last_batchs')
    last_done_batch = fields.Many2many('runbot.batch', 'Last batchs', compute='_compute_last_done_batch')

    sticky = fields.Boolean('Sticky', index=True)
    is_base = fields.Boolean('Is base', index=True)
    defined_base_id = fields.Many2one('runbot.bundle', 'Forced base bundle', domain="[('project_id', '=', project_id), ('is_base', '=', True)]")
    base_id = fields.Many2one('runbot.bundle', 'Base bundle', compute='_compute_base_id', store=True)

    version_id = fields.Many2one('runbot.version', 'Version', compute='_compute_version_id', store=True)
    version_number = fields.Char(related='version_id.number', store=True, index=True)

    previous_version_base_id = fields.Many2one('runbot.bundle', 'Previous base bundle', compute='_compute_previous_version_base_id')
    intermediate_version_base_ids = fields.Many2many('runbot.bundle', 'Intermediate base bundles', compute='_compute_previous_version_base_id')

    priority = fields.Boolean('Build priority', default=False)

    trigger_custom_ids = fields.One2many('runbot.trigger.custom', 'bundle_id')

    @api.depends('sticky')
    def _compute_make_stats(self):
        for bundle in self:
            bundle.make_stats = bundle.sticky

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
            else:
                bundle.base_id = self.env['ir.model.data'].xmlid_to_res_id('runbot.bundle_master')

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

    @api.depends_context('category_id')
    def _compute_last_batchs(self):
        if self:
            batch_ids = defaultdict(list)
            category_id = self.env.context.get('category_id', self.env['ir.model.data'].xmlid_to_res_id('runbot.default_category'))
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
                        AND batch.category_id = %s
                    ) AS bundle_batch
                WHERE
                    row <= 4
                ORDER BY row, id desc
                """, [tuple(self.ids), category_id] # TODO use context ?  make context dependant
            )
            batchs = self.env['runbot.batch'].browse([r[0] for r in self.env.cr.fetchall()])
            for batch in batchs:
                batch_ids[batch.bundle_id.id].append(batch.id)

            for bundle in self:
                bundle.last_batchs = [(6, 0, batch_ids[bundle.id])]

    @api.depends_context('category_id')
    def _compute_last_done_batch(self):
        if self:
            for bundle in self:
                bundle.last_done_batch = False
            category_id = self.env.context.get('category_id', self.env['ir.model.data'].xmlid_to_res_id('runbot.default_category'))
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
                        AND batch.state = 'done'
                        AND batch.category_id = %s
                    ) AS bundle_batch
                WHERE
                    row = 1
                ORDER BY row, id desc
                """, [tuple(self.ids), category_id]
            )
            batchs = self.env['runbot.batch'].browse([r[0] for r in self.env.cr.fetchall()])
            for batch in batchs:
                batch.bundle_id.last_done_batch = batch

    def create(self, values_list):
        # self.flush() # TODO check that
        return super().create(values_list)
        #if any(values.get('is_base') for values in values_list):
        #    (self.search([
        #        ('project_id', 'in', self.mapped('project_id').ids),
        #        ('is_base', '=', True)
        #    ]) + self)._compute_previous_version_base_id()

    def write(self, values):
        super().write(values)
        # self.flush() # TODO check that

    def _force(self):
        self.ensure_one()
        if self.last_batch.state == 'preparing':
            return
        new = self.env['runbot.batch'].create({
            'last_update': fields.Datetime.now(),
            'bundle_id': self.id,
            'state': 'preparing',
        })
        self.last_batch = new
        new.sudo()._prepare(force=True)
        return new

    def consistency_warning(self):
        if self.defined_base_id:
            return [('info', 'This bundle has a forced base: %s' % self.defined_base_id.name)]
        warnings = []
        for branch in self.branch_ids:
            if branch.is_pr and branch.target_branch_name != self.base_id.name:
                if branch.target_branch_name.startswith(self.base_id.name):
                    warnings.append(('info', 'PR %s targeting a non base branch: %s' % (branch.dname, branch.target_branch_name)))
                else:
                    warnings.append(('warning', 'PR %s targeting wrong version: %s (expecting %s)' % (branch.dname, branch.target_branch_name, self.base_id.name)))
            elif not branch.is_pr and not branch.name.startswith(self.base_id.name):
                warnings.append(('warning', 'Branch %s not strating with version name (%s)' % (branch.dname, self.base_id.name)))
        return warnings


class TriggerCustomisation(models.Model):
    _name = 'runbot.trigger.custom'
    _description = 'Custom trigger'

    trigger_id = fields.Many2one('runbot.trigger', domain="[('project_id', '=', bundle_id.project_id)]")
    bundle_id = fields.Many2one('runbot.bundle')
    config_id = fields.Many2one('runbot.build.config')

    _sql_constraints = [
        (
            "bundle_custom_trigger_unique",
            "unique (bundle_id, trigger_id)",
            "Only one custom trigger per trigger per bundle is allowed",
        )
    ]

class Batch(models.Model):
    _name = "runbot.batch"
    _description = "Bundle batch"

    last_update = fields.Datetime('Last ref update')
    bundle_id = fields.Many2one('runbot.bundle', required=True, index=True, ondelete='cascade')
    commit_link_ids = fields.Many2many('runbot.commit.link')
    slot_ids = fields.One2many('runbot.batch.slot', 'batch_id')
    state = fields.Selection([('preparing', 'Preparing'), ('ready', 'Ready'), ('done', 'Done')])
    hidden = fields.Boolean('Hidden', default=False)
    age = fields.Integer(compute='_compute_age', string='Build age')
    category_id = fields.Many2one('runbot.trigger.category', default=lambda self: self.env.ref('runbot.default_category', raise_if_not_found=False))
    log_ids = fields.One2many('runbot.batch.log', 'batch_id')

    @api.depends('create_date')
    def _compute_age(self):
        """Return the time between job start and now"""
        for batch in self:
            if batch.create_date:
                batch.age = int(time.time() - dt2time(batch.create_date))
            else:
                batch.buildage_age = 0

    def get_formated_age(self):
        return s2human_long(self.age)

    def _url(self):
        self.ensure_one()
        runbot_domain = self.env['runbot.runbot']._domain()
        return "http://%s/runbot/batch/%s" % (runbot_domain, self.id)

    def _new_commit(self, branch, match_type='new'):
        # if not the same hash for repo:
        commit = branch.head
        self.last_update = fields.Datetime.now()
        for commit_link in self.commit_link_ids:
            # case 1: a commit already exists for the repo (pr+branch, or fast push)
            if commit_link.commit_id.repo_id == commit.repo_id:
                if commit_link.commit_id.id != commit.id:
                    self.log('New head on branch %s during throttle phase: Replacing commit %s with %s', branch.name, commit_link.commit_id.name, commit.name)
                    commit_link.write({'commit_id': commit.id, 'branch_id': branch.id})
                elif not commit_link.branch_id.is_pr and branch.is_pr:
                    commit_link.branch_id = branch  # Try to have a pr instead of branch on commit if possible ?
                break
        else:
            self.write({'commit_link_ids': [(0, 0, {
                'commit_id': commit.id,
                'match_type': match_type,
                'branch_id': branch.id
            })]})

    def _skip(self):
        for batch in self:
            if batch.bundle_id.is_base or batch.state == 'done':
                continue
            batch.state = 'done'
            batch.log('Skipping batch')
            for slot in batch.slot_ids:
                slot.skipped = False
                build = slot.build_id
                testing_slots = build.slot_ids.filtered(lambda s: not s.skipped)
                if not testing_slots:  # TODO check that active test is used
                    if build.global_state == 'pending':
                        build._skip('Newer build found')
                    elif build.global_state in ('waiting', 'testing'):
                        build.killable = True
                elif slot.link_type == 'created':
                    batches = testing_slots.mapped('batch_id')
                    _logger.info('Cannot skip build %s build is still in use in batches %s', build.id, batches.ids)
                    bundles = batches.mapped('bundle_id') - batch.bundle_id
                    if bundles:
                        batch.log('Cannot kill or skip build %s, build is used in another bundle: %s', build.id, bundles.mapped('name'))

    def _process(self):
        for batch in self:
            if batch.state == 'preparing' and batch.last_update < fields.Datetime.now() - datetime.timedelta(seconds=60):
                batch._prepare()
            elif batch.state == 'ready' and all(slot.build_id.global_state in (False, 'running', 'done') for slot in batch.slot_ids):
                batch.log('Batch done')
                batch.state = 'done'

    def _prepare(self, force=False):
        self.state = 'ready'
        _logger.info('Preparing batch %s', self.id)
        project = self.bundle_id.project_id
        triggers = self.env['runbot.trigger'].search([  # could be optimised for multiple batches. Ormcached method?
            ('project_id', '=', project.id),
            ('category_id', '=', self.category_id.id),
            '|',
                ('version_ids', 'in', self.bundle_id.version_id.id),  # upgrade trigger are only used in master version
                ('version_ids', '=', False)
        ])
        pushed_repo = self.commit_link_ids.mapped('commit_id.repo_id')
        dependency_repos = triggers.mapped('dependency_ids')
        all_repos = triggers.mapped('repo_ids') | dependency_repos
        missing_repos = all_repos - pushed_repo

        # find missing commits on bundle branches head
        def fill_missing(branch_commits, match_type):
            if branch_commits:
                for branch, commit in branch_commits.items(): # branch first in case pr is closed.
                    nonlocal missing_repos
                    if commit.repo_id in missing_repos:
                        values = {
                            'commit_id': commit.id,
                            'match_type': match_type,
                            'branch_id': branch.id,
                        }
                        if match_type.startswith('base'):
                            values['base_commit_id'] = commit.id
                            values['merge_base_commit_id'] = commit.id
                        self.write({'commit_link_ids': [(0, 0, values)]})
                        missing_repos -= commit.repo_id
                        # TODO manage multiple branch in same repo: chose best one and
                        # add warning if different commit are found
                        # add warning if bundle has warnings

        bundle = self.bundle_id
        # CHECK branch heads consistency
        branch_per_repo = {}
        for branch in bundle.branch_ids.sorted('is_pr', reverse=True):
            commit = branch.head
            repo = commit.repo_id
            if not repo in branch_per_repo:
                branch_per_repo[repo] = branch
            elif branch_per_repo[repo].head != branch.head:
                obranch = branch_per_repo[repo]
                self.warning("Branch %s and branch %s in repo %s don't have the same head: %s ≠ %s", branch.dname, obranch.dname, repo.name, branch.head.name, obranch.head.name)

        # 1.1 FIND missing commit in bundle heads
        if missing_repos:
            fill_missing({branch: branch.head for branch in bundle.branch_ids.sorted(lambda b: (b.head.id, b.is_pr), reverse=True)}, 'head')

        # 1.2 FIND merge_base info for those commits
        #  use last not preparing batch to define previous repos_heads instead of branches heads:
        #  Will allow to have a diff info on base bundle, compare with previous bundle
        last_base_batch = self.env['runbot.batch'].search([('bundle_id', '=', bundle.base_id.id), ('state', '!=', 'preparing'), ('id', '!=', self.id)], order='id desc', limit=1)
        print(last_base_batch)
        base_head_per_repo = {commit.repo_id.id: commit for commit in last_base_batch.commit_link_ids.mapped('commit_id')}
        self._update_commits_infos(base_head_per_repo)  # set base_commit, diff infos, ...

        # 2. FIND missing commit in a compatible base bundle
        if missing_repos and not bundle.is_base:
            merge_base_commits = self.commit_link_ids.mapped('merge_base_commit_id')
            link_commit = self.env['runbot.commit.link'].search([
                ('commit_id', 'in', merge_base_commits.ids),
                ('match_type', 'in', ('new', 'head'))
            ])
            batches = self.env['runbot.batch'].search([
                ('bundle_id', '=', bundle.base_id.id),
                ('commit_link_ids', 'in', link_commit.ids),
                ('state', '!=', 'preparing')
            ])
            if batches:
                batches = batches.sorted(lambda b: (len(b.commit_link_ids.mapped('commit_id') & merge_base_commits), b.id), reverse=True)
                batch = batches[0]
                self.log('Using batch %s to define missing commits', batch.id)
                matched = batch.commit_link_ids.mapped('commit_id') & merge_base_commits
                if len(matched) != len(merge_base_commits):
                    self.warning('Only %s out of %s merge base matched. You may want to rebase your branches to ensure compatibility', len(matched), len(merge_base_commits) )
                fill_missing({branch: branch.head for branch in batch.commit_link_ids.mapped('branch_id')}, 'base_match')

        # 3. FIND missing commit in base heads
        if missing_repos:
            if not bundle.is_base:
                self.log('Not all commit found in bundle branches and base batch. Fallback on base branches heads.')
            fill_missing({branch: branch.head for branch in self.bundle_id.base_id.branch_ids}, 'base_head')

        # 4. FIND missing commit in foreign project
        if missing_repos:
            foreign_projects = dependency_repos.mapped('project_id') - project
            if foreign_projects:
                self.log('Not all commit found. Fallback on foreign base branches heads.')
                foreign_bundles = bundle.search([('name', '=', bundle.name), ('project_id', 'in', foreign_projects.ids)])
                fill_missing({branch: branch.head for branch in foreign_bundles.mapped('branch_ids').sorted('is_pr', reverse=True)}, 'head')
                if missing_repos:
                    foreign_bundles = bundle.search([('name', '=', bundle.base_id.name), ('project_id', 'in', foreign_projects.ids)])
                    fill_missing({branch: branch.head for branch in foreign_bundles.mapped('branch_ids')}, 'base_head')

        # CHECK missing commit
        if missing_repos:
            _logger.warning('Missing repo %s for batch %s', missing_repos.mapped('name'), self.id)


        # CREATE builds
        commit_link_by_repos = {commit_link.commit_id.repo_id.id: commit_link for commit_link in self.commit_link_ids}
        version_id = self.bundle_id.version_id.id
        project_id = self.bundle_id.project_id.id
        config_by_trigger = {}
        for trigger_custom in self.bundle_id.trigger_custom_ids:
            config_by_trigger[trigger_custom.trigger_id.id] = trigger_custom.config_id
        for trigger in triggers:
            link_type = 'created'
            build = self.env['runbot.build']
            trigger_repos = trigger.repo_ids | trigger.dependency_ids
            if trigger_repos & missing_repos:
                self.warning('Missing commit for repo %s for trigger %s', (trigger_repos & missing_repos).mapped('name'), trigger.name)
                continue
            # in any case, search for an existing build
            config = config_by_trigger.get(trigger.id, trigger.config_id)

            params_value = {
                'version_id':  version_id,
                'extra_params': '',
                'config_id': config.id,
                'project_id': project_id,
                'trigger_id': trigger.id,  # for future reference and access rights
                'config_data': {},
                'commit_link_ids': [(6, 0, [commit_link_by_repos[repo.id].id for repo in trigger_repos])],
                'builds_reference_ids': [],  # TODO
                'modules': bundle.modules
            }
            params = self.env['runbot.build.params'].create(params_value)
            build = self.env['runbot.build'].search([('params_id', '=', params.id), ('parent_id', '=', False)], limit=1, order='id desc')
            # id desc will take the most recent one if multiple build. Hopefully it is a green build.
            # TODO sort on result?
            if build:
                link_type = 'matched'
                build.killable = False
            elif (trigger.repo_ids & pushed_repo) or force or bundle.build_all or bundle.sticky: # common repo between triggers and pushed
                build = self.env['runbot.build'].create({
                    'params_id': params.id,
                })
            self.env['runbot.batch.slot'].create({
                'batch_id': self.id,
                'trigger_id': trigger.id,
                'build_id': build.id,
                'params_id': params.id,
                'link_type': link_type,
            })

        # SKIP older batches
        default_category = self.env.ref('runbot.default_category')
        if not bundle.sticky and self.category_id == default_category:
            skippable = self.env['runbot.batch'].search([
                ('bundle_id', '=', bundle.id),
                ('state', '!=', 'done'),
                ('id', '<', self.id),
                ('category_id', '=', default_category.id)
            ])
            skippable._skip()

    def _update_commits_infos(self, base_head_per_repo):
        for link_commit in self.commit_link_ids:
            commit = link_commit.commit_id
            base_head = base_head_per_repo.get(commit.repo_id.id)
            if not base_head:
                self.warning('No base head found for repo %s', commit.repo_id.name)
                continue
            link_commit.base_commit_id = base_head
            merge_base_sha = False
            try:
                link_commit.base_ahead = link_commit.base_behind = 0
                link_commit.file_changed = link_commit.diff_add = link_commit.diff_remove = 0
                link_commit.merge_base_commit_id = commit.id
                if commit.name == base_head.name:
                    continue
                merge_base_sha = commit.repo_id._git(['merge-base', commit.name, base_head.name]).strip()
                merge_base_commit = self.env['runbot.commit'].search([('name', '=', merge_base_sha), ('repo_id', '=', commit.repo_id.id)])
                if not merge_base_commit:
                    merge_base_commit = self.env['runbot.commit'].create({'name': merge_base_sha, 'repo_id': commit.repo_id.id})
                    self.warning('Commit for base head %s in %s was created', merge_base_sha, commit.repo_id.name)
                link_commit.merge_base_commit_id = merge_base_commit.id


                ahead, behind = commit.repo_id._git(['rev-list', '--left-right', '--count', '%s...%s' % (commit.name, base_head.name)]).strip().split('\t')

                link_commit.base_ahead = int(ahead)
                link_commit.base_behind = int(behind)

                if merge_base_sha == commit.name:
                    continue

                # diff. Iter on --numstat, easier to parse than --shortstat summary
                diff = commit.repo_id._git(['diff', '--numstat', merge_base_sha, commit.name]).strip()
                if diff:
                    for line in diff.split('\n'):
                        link_commit.file_changed += 1
                        add, remove, _ = line.split(None, 2)
                        try:
                            link_commit.diff_add += int(add)
                            link_commit.diff_remove += int(remove)
                        except ValueError:  # binary files
                            pass
            except subprocess.CalledProcessError:
                self.warning('Commit info failed between %s and %s', commit.name, base_head.name)


    def warning(self, message, *args):
        _logger.warning('batch %s: ' + message, self.id, *args)
        self.log(message, *args, level='WARNING')

    def log(self, message, *args, level='INFO'):
        self.env['runbot.batch.log'].create({
            'batch_id': self.id,
            'message': message % args if args else message,
            'level': level,
        })

class BatchLog(models.Model):
    _name = 'runbot.batch.log'
    _description = 'Batch log'

    batch_id = fields.Many2one('runbot.batch', index=True)
    message = fields.Char('Message')
    level = fields.Char()


class BatchSlot(models.Model):
    _name = 'runbot.batch.slot'
    _description = 'Link between a bundle batch and a build'

    _fa_link_type = {'created': 'hashtag', 'matched': 'link', 'rebuild': 'refresh'}

    batch_id = fields.Many2one('runbot.batch', index=True)
    trigger_id = fields.Many2one('runbot.trigger', index=True)
    build_id = fields.Many2one('runbot.build', index=True)
    params_id = fields.Many2one('runbot.build.params', index=True, required=True)
    link_type = fields.Selection([('created', 'Build created'), ('matched', 'Existing build matched'), ('rebuild', 'Rebuild')], required=True) # rebuild type?
    active = fields.Boolean('Attached', default=True)
    skipped = fields.Boolean('Skipped', default=False)
    # rebuild, what to do: since build ccan be in multiple batch:
    # - replace for all batch?
    # - only available on batch and replace for batch only?
    # - create a new bundle batch will new linked build?

    def fa_link_type(self):
        return self._fa_link_type.get(self.link_type, 'exclamation-triangle')


class RunbotCommitLink(models.Model):
    _name = "runbot.commit.link"
    _description = "Build commit"

    commit_id = fields.Many2one('runbot.commit', 'Commit', required=True, index=True)
    # Link info
    match_type = fields.Selection([('new', 'New head of branch'), ('head', 'Head of branch'), ('base_head', 'Found on base branch'), ('base_match', 'Found on base branch')])  # HEAD, DEFAULT
    branch_id = fields.Many2one('runbot.branch', string='Found in branch')  # Shouldn't be use for anything else than display

    base_commit_id = fields.Many2one('runbot.commit', 'Base head commit')
    merge_base_commit_id = fields.Many2one('runbot.commit', 'Merge Base commit')
    base_behind = fields.Integer('# commits behind base')
    base_ahead = fields.Integer('# commits ahead base')
    file_changed = fields.Integer('# file changed')
    diff_add = fields.Integer('# line added')
    diff_remove = fields.Integer('# line removed')
