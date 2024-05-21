from __future__ import annotations

import ast
import base64
import contextlib
import logging
import os
import re
import typing
from collections.abc import Iterator

import requests

from odoo import models, fields, api
from .pull_requests import PullRequests, Branch
from .utils import enum


_logger = logging.getLogger(__name__)
FOOTER = '\nMore info at https://github.com/odoo/odoo/wiki/Mergebot#forward-port\n'


class Batch(models.Model):
    """ A batch is a "horizontal" grouping of *codependent* PRs: PRs with
    the same label & target but for different repositories. These are
    assumed to be part of the same "change" smeared over multiple
    repositories e.g. change an API in repo1, this breaks use of that API
    in repo2 which now needs to be updated.
    """
    _name = 'runbot_merge.batch'
    _description = "batch of pull request"
    _inherit = ['mail.thread']
    _parent_store = True

    target = fields.Many2one('runbot_merge.branch', store=True, compute='_compute_target')
    staging_ids = fields.Many2many('runbot_merge.stagings')
    split_id = fields.Many2one('runbot_merge.split', index=True)

    prs = fields.One2many('runbot_merge.pull_requests', 'batch_id')

    fw_policy = fields.Selection([
        ('default', "Default"),
        ('skipci', "Skip CI"),
    ], required=True, default="default", string="Forward Port Policy")

    merge_date = fields.Datetime(tracking=True)
    # having skipchecks skip both validation *and approval* makes sense because
    # it's batch-wise, having to approve individual PRs is annoying
    skipchecks = fields.Boolean(
        string="Skips Checks",
        default=False, tracking=True,
        help="Forces entire batch to be ready, skips validation and approval",
    )
    cancel_staging = fields.Boolean(
        string="Cancels Stagings",
        default=False, tracking=True,
        help="Cancels current staging on target branch when becoming ready"
    )
    priority = fields.Selection([
        ('default', "Default"),
        ('priority', "Priority"),
        ('alone', "Alone"),
    ], default='default', group_operator=None, required=True,
        column_type=enum(_name, 'priority'),
    )

    blocked = fields.Char(store=True, compute="_compute_stageable")

    # unlike on PRs, this does not get detached... ? (because batches can be
    # partially detached so that's a PR-level concern)
    parent_path = fields.Char(index=True)
    parent_id = fields.Many2one("runbot_merge.batch")
    genealogy_ids = fields.Many2many(
        "runbot_merge.batch",
        compute="_compute_genealogy",
        context={"active_test": False},
    )

    @property
    def source(self):
        return self.browse(map(int, self.parent_path.split('/', 1)[:1]))

    def descendants(self, include_self: bool = False) -> Iterator[Batch]:
        # in DB both will prefix-match on the literal prefix then apply a
        # trivial filter (even though the filter is technically unnecessary for
        # the first form), doing it like this means we don't have to `- self`
        # in the `not includ_self` case
        if include_self:
            pattern = self.parent_path + '%'
        else:
            pattern = self.parent_path + '_%'

        return self.search([("parent_path", '=like', pattern)])

    # also depends on all the descendants of the source or sth
    @api.depends('parent_path')
    def _compute_genealogy(self):
        for batch in self:
            sid = next(iter(batch.parent_path.split('/', 1)))
            batch.genealogy_ids = self.search([("parent_path", "=like", f"{sid}/%")], order="parent_path")

    def _auto_init(self):
        for field in self._fields.values():
            if not isinstance(field, fields.Selection) or field.column_type[0] == 'varchar':
                continue

            t = field.column_type[1]
            self.env.cr.execute("SELECT FROM pg_type WHERE typname = %s", [t])
            if not self.env.cr.rowcount:
                self.env.cr.execute(
                    f"CREATE TYPE {t} AS ENUM %s",
                    [tuple(s for s, _ in field.selection)]
                )

        super()._auto_init()

        self.env.cr.execute("""
        CREATE INDEX IF NOT EXISTS runbot_merge_batch_ready_idx
        ON runbot_merge_batch (target, priority)
        WHERE blocked IS NULL;

        CREATE INDEX IF NOT EXISTS runbot_merge_batch_parent_id_idx
        ON runbot_merge_batch (parent_id)
        WHERE parent_id IS NOT NULL;
        """)

    @api.depends("prs.target")
    def _compute_target(self):
        for batch in self:
            if len(batch.prs) == 1:
                batch.target = batch.prs.target
            else:
                targets = set(batch.prs.mapped('target'))
                if not targets:
                    targets = set(batch.prs.mapped('target'))
                if len(targets) == 1:
                    batch.target = targets.pop()
                else:
                    batch.target = False


    @api.depends(
        "merge_date",
        "prs.error", "prs.draft", "prs.squash", "prs.merge_method",
        "skipchecks", "prs.status", "prs.reviewed_by",
        "prs.target"
    )
    def _compute_stageable(self):
        for batch in self:
            if batch.merge_date:
                batch.blocked = "Merged."
            elif blocking := batch.prs.filtered(
                lambda p: p.error or p.draft or not (p.squash or p.merge_method)
            ):
                batch.blocked = "Pull request(s) %s blocked." % ', '.join(blocking.mapped('display_name'))
            elif not batch.skipchecks and (unready := batch.prs.filtered(
                lambda p: not (p.reviewed_by and p.status == "success")
            )):
                unreviewed = ', '.join(unready.filtered(lambda p: not p.reviewed_by).mapped('display_name'))
                unvalidated = ', '.join(unready.filtered(lambda p: p.status == 'pending').mapped('display_name'))
                failed = ', '.join(unready.filtered(lambda p: p.status == 'failure').mapped('display_name'))
                batch.blocked = "Pull request(s) %s." % ', '.join(filter(None, [
                    unreviewed and f"{unreviewed} are waiting for review",
                    unvalidated and f"{unvalidated} are waiting for CI",
                    failed and f"{failed} have failed CI",
                ]))
            elif len(targets := batch.prs.mapped('target')) > 1:
                batch.blocked = f"Multiple target branches: {', '.join(targets.mapped('name'))!r}"
            else:
                if batch.blocked and batch.cancel_staging:
                    batch.target.active_staging_id.cancel(
                        'unstaged by %s on %s (%s)',
                        self.env.user.login,
                        batch,
                        ', '.join(batch.prs.mapped('display_name')),
                    )
                batch.blocked = False


    def _port_forward(self):
        if not self:
            return

        proj = self.target.project_id
        if not proj.fp_github_token:
            _logger.warning(
                "Can not forward-port %s (%s): no token on project %s",
                self,
                ', '.join(self.prs.mapped('display_name')),
                proj.name
            )
            return

        notarget = [r.name for r in self.prs.repository if not r.fp_remote_target]
        if notarget:
            _logger.error(
                "Can not forward-port %s (%s): repos %s don't have a forward port remote configured",
                self,
                ', '.join(self.prs.mapped('display_name')),
                ', '.join(notarget),
            )
            return

        all_sources = [(p.source_id or p) for p in self.prs]
        all_targets = [p._find_next_target() for p in self.prs]

        if all(t is None for t in all_targets):
            # TODO: maybe add a feedback message?
            _logger.info(
                "Will not forward port %s (%s): no next target",
                self,
                ', '.join(self.prs.mapped('display_name'))
            )
            return

        for p, t in zip(self.prs, all_targets):
            if t is None:
                _logger.info("Skip forward porting %s (of %s): no next target", p.display_name, self)

        # all the PRs *with a next target* should have the same, we can have PRs
        # stopping forward port earlier but skipping... probably not
        targets = set(filter(None, all_targets))
        if len(targets) != 1:
            m = dict(zip(all_targets, self.prs))
            m.pop(None, None)
            mm = dict(zip(self.prs, all_targets))
            for pr in self.prs:
                t = mm[pr]
                # if a PR is skipped, don't flag it for discrepancy
                if not t:
                    continue

                other, linked = next((target, p) for target, p in m.items() if target != t)
                self.env.ref('runbot_merge.forwardport.failure.discrepancy')._send(
                    repository=pr.repository,
                    pull_request=pr.number,
                    token_field='fp_github_token',
                    format_args={'pr': pr, 'linked': linked, 'next': t.name, 'other': other.name},
                )
            _logger.warning(
                "Cancelling forward-port of %s (%s): found different next branches (%s)",
                self,
                ', '.join(self.prs.mapped('display_name')),
                ', '.join(t.name for t in targets),
            )
            return

        target = targets.pop()
        # this is run by the cron, no need to check if otherwise scheduled:
        # either the scheduled job is this one, or it's an other scheduling
        # which will run after this one and will see the port already exists
        if self.search_count([('parent_id', '=', self.id), ('target', '=', target.id)]):
            _logger.warning(
                "Will not forward-port %s (%s): already ported",
                self,
                ', '.join(self.prs.mapped('display_name'))
            )
            return

        # the base PR is the PR with the "oldest" target
        base = max(all_sources, key=lambda p: (p.target.sequence, p.target.name))
        # take only the branch bit
        new_branch = '%s-%s-%s-fw' % (
            target.name,
            base.refname,
            # avoid collisions between fp branches (labels can be reused
            # or conflict especially as we're chopping off the owner)
            base64.urlsafe_b64encode(os.urandom(3)).decode()
        )
        conflicts = {}
        with contextlib.ExitStack() as s:
            for pr in self.prs:
                conflicts[pr], working_copy = pr._create_fp_branch(
                    target, new_branch, s)

                working_copy.push('target', new_branch)

        gh = requests.Session()
        gh.headers['Authorization'] = 'token %s' % proj.fp_github_token
        has_conflicts = any(conflicts.values())
        # could create a batch here but then we'd have to update `_from_gh` to
        # take a batch and then `create` to not automatically resolve batches,
        # easier to not do that.
        new_batch = self.env['runbot_merge.pull_requests'].browse(())
        self.env.cr.execute('LOCK runbot_merge_pull_requests IN SHARE MODE')
        for pr in self.prs:
            owner, _ = pr.repository.fp_remote_target.split('/', 1)
            source = pr.source_id or pr
            root = pr.root_id

            message = source.message + '\n\n' + '\n'.join(
                "Forward-Port-Of: %s" % p.display_name
                for p in root | source
            )

            title, body = re.match(r'(?P<title>[^\n]+)\n*(?P<body>.*)', message, flags=re.DOTALL).groups()
            r = gh.post(f'https://api.github.com/repos/{pr.repository.name}/pulls', json={
                'base': target.name,
                'head': f'{owner}:{new_branch}',
                'title': '[FW]' + (' ' if title[0] != '[' else '') + title,
                'body': body
            })
            if not r.ok:
                _logger.warning("Failed to create forward-port PR for %s, deleting branches", pr.display_name)
                # delete all the branches this should automatically close the
                # PRs if we've created any. Using the API here is probably
                # simpler than going through the working copies
                for repo in self.prs.mapped('repository'):
                    d = gh.delete(f'https://api.github.com/repos/{repo.fp_remote_target}/git/refs/heads/{new_branch}')
                    if d.ok:
                        _logger.info("Deleting %s:%s=success", repo.fp_remote_target, new_branch)
                    else:
                        _logger.warning("Deleting %s:%s=%s", repo.fp_remote_target, new_branch, d.text)
                raise RuntimeError("Forwardport failure: %s (%s)" % (pr.display_name, r.text))

            new_pr = pr._from_gh(r.json())
            _logger.info("Created forward-port PR %s", new_pr)
            new_batch |= new_pr

            # allows PR author to close or skipci
            new_pr.write({
                'merge_method': pr.merge_method,
                'source_id': source.id,
                # only link to previous PR of sequence if cherrypick passed
                'parent_id': pr.id if not has_conflicts else False,
                'detach_reason': "conflicts: {}".format(
                    f'\n{conflicts[pr]}\n{conflicts[pr]}'.strip()
                ) if has_conflicts else None,
            })
            if has_conflicts and pr.parent_id and pr.state not in ('merged', 'closed'):
                self.env.ref('runbot_merge.forwardport.failure.conflict')._send(
                    repository=pr.repository,
                    pull_request=pr.number,
                    token_field='fp_github_token',
                    format_args={'source': source, 'pr': pr, 'new': new_pr, 'footer': FOOTER},
                )

        for pr, new_pr in zip(self.prs, new_batch):
            (h, out, err, hh) = conflicts.get(pr) or (None, None, None, None)

            if h:
                sout = serr = ''
                if out.strip():
                    sout = f"\nstdout:\n```\n{out}\n```\n"
                if err.strip():
                    serr = f"\nstderr:\n```\n{err}\n```\n"

                lines = ''
                if len(hh) > 1:
                    lines = '\n' + ''.join(
                        '* %s%s\n' % (sha, ' <- on this commit' if sha == h else '')
                        for sha in hh
                    )
                template = 'runbot_merge.forwardport.failure'
                format_args = {
                    'pr': new_pr,
                    'commits': lines,
                    'stdout': sout,
                    'stderr': serr,
                    'footer': FOOTER,
                }
            elif has_conflicts:
                template = 'runbot_merge.forwardport.linked'
                format_args = {
                    'pr': new_pr,
                    'siblings': ', '.join(p.display_name for p in (new_batch - new_pr)),
                    'footer': FOOTER,
                }
            elif not new_pr._find_next_target():
                ancestors = "".join(
                    f"* {p.display_name}\n"
                    for p in pr._iter_ancestors()
                    if p.parent_id
                )
                template = 'runbot_merge.forwardport.final'
                format_args = {
                    'pr': new_pr,
                    'containing': ' containing:' if ancestors else '.',
                    'ancestors': ancestors,
                    'footer': FOOTER,
                }
            else:
                template = 'runbot_merge.forwardport.intermediate'
                format_args = {
                    'pr': new_pr,
                    'footer': FOOTER,
                }
            self.env.ref(template)._send(
                repository=new_pr.repository,
                pull_request=new_pr.number,
                token_field='fp_github_token',
                format_args=format_args,
            )

            labels = ['forwardport']
            if has_conflicts:
                labels.append('conflict')
            self.env['runbot_merge.pull_requests.tagging'].create({
                'repository': new_pr.repository.id,
                'pull_request': new_pr.number,
                'tags_add': labels,
            })

        new_batch = new_batch.batch_id
        new_batch.parent_id = self
        # try to schedule followup
        new_batch._schedule_fp_followup()
        return new_batch


    def _schedule_fp_followup(self):
        _logger = logging.getLogger(__name__).getChild('forwardport.next')
        # if the PR has a parent and is CI-validated, enqueue the next PR
        scheduled = self.browse(())
        for batch in self:
            prs = ', '.join(batch.prs.mapped('display_name'))
            _logger.info('Checking if forward-port %s (%s)', batch, prs)
            # in cas of conflict or update individual PRs will "lose" their
            # parent, which should prevent forward porting
            if not (batch.parent_id and all(p.parent_id for p in batch.prs)):
                _logger.info('-> no parent %s (%s)', batch, prs)
                continue
            if not self.env.context.get('force_fw') and self.source.fw_policy != 'skipci' \
                    and (invalid := batch.prs.filtered(lambda p: p.state not in ['validated', 'ready'])):
                _logger.info(
                    '-> wrong state %s (%s)',
                    batch,
                    ', '.join(f"{p.display_name}: {p.state}" for p in invalid),
                )
                continue

            # check if we've already forward-ported this branch
            next_target = self._find_next_targets()
            if not next_target:
                _logger.info("-> forward port done (no next target)")
                continue
            if len(next_target) > 1:
                _logger.error(
                    "-> cancelling forward-port of %s (%s): inconsistent next target branch (%s)",
                    batch,
                    prs,
                    ', '.join(next_target.mapped('name')),
                )

            if n := self.search([
                ('target', '=', next_target.id),
                ('parent_id', '=', batch.id),
            ], limit=1):
                _logger.info('-> already forward-ported (%s)', n)
                continue

            _logger.info("check pending port for %s (%s)", batch, prs)
            if self.env['forwardport.batches'].search_count([('batch_id', '=', batch.id)]):
                _logger.warning('-> already recorded')
                continue

            _logger.info('-> ok')
            self.env['forwardport.batches'].create({
                'batch_id': batch.id,
                'source': 'fp',
            })
            scheduled |= batch
        return scheduled

    def _find_next_target(self):
        """Retrieves the next target from every PR, and returns it if it's the
        same for all the PRs which have one (PRs without a next target are
        ignored, this is considered acceptable).

        If the next targets are inconsistent, returns no next target.
        """
        next_target = self._find_next_targets()
        if len(next_target) == 1:
            return next_target
        else:
            return self.env['runbot_merge.branch'].browse(())

    def _find_next_targets(self):
        return self.prs.mapped(lambda p: p._find_next_target() or self.env['runbot_merge.branch'])

    def write(self, vals):
        if vals.get('merge_date'):
            # TODO: remove condition when everything is merged
            remover = self.env.get('forwardport.branch_remover')
            if remover is not None:
                remover.create([
                    {'pr_id': p.id}
                    for b in self
                    if not b.merge_date
                    for p in b.prs
                ])

        if vals.get('fw_policy') == 'skipci':
            nonskip = self.filtered(lambda b: b.fw_policy != 'skipci')
        else:
            nonskip = self.browse(())
        super().write(vals)

        # if we change the policy to skip CI, schedule followups on merged
        # batches which were not previously marked as skipping CI
        if nonskip:
            toggled = nonskip.filtered(lambda b: b.merge_date)
            tips = toggled.mapped(lambda b: b.genealogy_ids[-1:])
            for tip in tips:
                tip._schedule_fp_followup()

        return True
