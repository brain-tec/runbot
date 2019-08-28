# -*- coding: utf-8 -*-
"""
Technically could be independent from mergebot but would require a lot of
duplicate work e.g. keeping track of statuses (including on commits which
might not be in PRs yet), handling co-dependent PRs, ...

However extending the mergebot also leads to messiness: fpbot should have
its own user / feedback / API keys, mergebot and fpbot both have branch
ordering but for mergebot it's completely cosmetics, being slaved to mergebot
means PR creation is trickier (as mergebot assumes opened event will always
lead to PR creation but fpbot wants to attach meaning to the PR when setting
it up), ...
"""
import base64
import contextlib
import itertools
import json
import logging
import os
import pathlib
import subprocess
import tempfile

import re
import requests

from odoo.exceptions import UserError
from odoo.tools.appdirs import user_cache_dir

from odoo import _, models, fields, api
from odoo.addons.runbot_merge import utils
from odoo.addons.runbot_merge.models.pull_requests import RPLUS


_logger = logging.getLogger('odoo.addons.forwardport')

class Project(models.Model):
    _inherit = 'runbot_merge.project'

    fp_github_token = fields.Char()
    fp_github_name = fields.Char(store=True, compute="_compute_git_identity")
    fp_github_email = fields.Char(store=True, compute="_compute_git_identity")

    def _find_commands(self, comment):
        if self.env.context.get('without_forward_port'):
            return super()._find_commands(comment)

        return re.findall(
            '^\s*[@|#]?{}:? (.*)$'.format(self.fp_github_name),
            comment, re.MULTILINE | re.IGNORECASE
        ) + super()._find_commands(comment)

    # technically the email could change at any moment...
    @api.depends('fp_github_token')
    def _compute_git_identity(self):
        s = requests.Session()
        for project in self:
            if not project.fp_github_token:
                continue
            r0 = s.get('https://api.github.com/user', headers={
                'Authorization': 'token %s' % project.fp_github_token
            })
            if 'user:email' not in set(re.split(r',\s*', r0.headers['x-oauth-scopes'])):
                raise UserError(_("The forward-port github token needs the user:email scope to fetch the bot's identity."))
            r1 = s.get('https://api.github.com/user/emails', headers={
                'Authorization': 'token %s' % project.fp_github_token
            })
            if not (r0.ok and r1.ok):
                _logger.warn("Failed to fetch bot information for project %s: %s", project.name, (r0.text or r0.content) if not r0.ok else (r1.text or r1.content))
                continue
            project.fp_github_name = r0.json()['login']
            project.fp_github_email = next((
                entry['email']
                for entry in r1.json()
                if entry['primary']
            ), None)
            if not project.fp_github_email:
                raise UserError(_("The forward-port bot needs a primary email set up."))


class Repository(models.Model):
    _inherit = 'runbot_merge.repository'
    fp_remote_target = fields.Char(help="where FP branches get pushed")

class Branch(models.Model):
    _inherit = 'runbot_merge.branch'

    fp_sequence = fields.Integer(default=50)
    fp_target = fields.Boolean(default=False)
    fp_enabled = fields.Boolean(compute='_compute_fp_enabled')

    @api.depends('active', 'fp_target')
    def _compute_fp_enabled(self):
        for b in self:
            b.fp_enabled = b.active and b.fp_target

    # FIXME: this should be per-project...
    def _forward_port_ordered(self):
        """ Returns all branches in forward port order (from the lowest to
        the highest — usually master)
        """
        return self.search([], order=self._forward_port_ordering())

    def _forward_port_ordering(self):
        return ','.join(
            f[:-5] if f.lower().endswith(' desc') else f + ' desc'
            for f in re.split(r',\s*', 'fp_sequence,' + self._order)
        )

class PullRequests(models.Model):
    _inherit = 'runbot_merge.pull_requests'

    # TODO: delete remote branches of merged FP PRs

    # QUESTION: should the limit be copied on each child, or should it be inferred from the parent? Also what happens when detaching, is the detached PR configured independently?
    # QUESTION: what happens if the limit_id is deactivated with extant PRs?
    limit_id = fields.Many2one(
        'runbot_merge.branch',
        default=lambda self: self.env['runbot_merge.branch']._forward_port_ordered()[-1],
        help="Up to which branch should this PR be forward-ported"
    )

    parent_id = fields.Many2one(
        'runbot_merge.pull_requests', index=True,
        help="a PR with a parent is an automatic forward port"
    )
    source_id = fields.Many2one('runbot_merge.pull_requests', index=True, help="the original source of this FP even if parents were detached along the way")

    refname = fields.Char(compute='_compute_refname')
    @api.depends('label')
    def _compute_refname(self):
        for pr in self:
            pr.refname = pr.label.split(':', 1)[-1]

    def create(self, vals):
        # PR opened event always creates a new PR, override so we can precreate PRs
        existing = self.search([
            ('repository', '=', vals['repository']),
            ('number', '=', vals['number']),
        ])
        if existing:
            return existing

        if vals.get('parent_id') and 'source_id' not in vals:
            vals['source_id'] = self.browse(vals['parent_id'])._get_root().id
        return super().create(vals)

    def write(self, vals):
        # if the PR's head is updated, detach (should split off the FP lines as this is not the original code)
        # TODO: better way to do this? Especially because we don't want to
        #       recursively create updates
        # also a bit odd to only handle updating 1 head at a time, but then
        # again 2 PRs with same head is weird so...
        newhead = vals.get('head')
        if newhead and not self.env.context.get('ignore_head_update') and newhead != self.head:
            vals.setdefault('parent_id', False)
            # if any children, this is an FP PR being updated, enqueue
            # updating children
            if self.search_count([('parent_id', '=', self.id)]):
                self.env['forwardport.updates'].create({
                    'original_root': self._get_root().id,
                    'new_root': self.id
                })

        if vals.get('parent_id') and 'source_id' not in vals:
            vals['source_id'] = self.browse(vals['parent_id'])._get_root().id
        return super().write(vals)

    def _try_closing(self, by):
        r = super()._try_closing(by)
        if r:
            self.parent_id = False
        return r

    def _parse_commands(self, author, comment, login):
        super(PullRequests, self.with_context(without_forward_port=True))._parse_commands(author, comment, login)

        tokens = [
            token
            for line in re.findall('^\s*[@|#]?{}:? (.*)$'.format(self.repository.project_id.fp_github_name), comment, re.MULTILINE | re.IGNORECASE)
            for token in line.split()
        ]
        if not tokens:
            _logger.info("found no commands in comment of %s (%s) (%s)", author.github_login, author.display_name,
                 utils.shorten(comment, 50)
            )
            return

        # TODO: don't use a mutable tokens iterator
        tokens = iter(tokens)
        for token in tokens:
            if token in ('r+', 'review+') and self.source_id._pr_acl(author).is_reviewer:
                # don't update the root ever
                for pr in filter(lambda p: p.parent_id, self._iter_ancestors()):
                    newstate = RPLUS.get(pr.state)
                    if newstate:
                        pr.state = newstate
                        pr.reviewed_by = author
                        # TODO: logging & feedback
            elif token == 'up' and next(tokens, None) == 'to' and self._pr_acl(author).is_reviewer:
                limit = next(tokens, None)
                if not limit:
                    msg = "Please provide a branch to forward-port to."
                else:
                    limit_id = self.env['runbot_merge.branch'].with_context(active_test=False).search([
                        ('project_id', '=', self.repository.project_id.id),
                        ('name', '=', limit),
                    ])
                    if not limit_id:
                        msg = "There is no branch %r, it can't be used as a forward port target." % limit
                    elif not limit_id.fp_enabled:
                        msg = "Branch %r is disabled, it can't be used as a forward port target." % limit_id.name
                    else:
                        msg = "Forward-porting to %r." % limit_id.name
                        self.limit_id = limit_id

                self.env['runbot_merge.pull_requests.feedback'].create({
                    'repository': self.repository.id,
                    'pull_request': self.number,
                    'message': msg,
                })

    def _validate(self, statuses):
        _logger = logging.getLogger(__name__).getChild('forwardport.next')
        super()._validate(statuses)
        # if the PR has a parent and is CI-validated, enqueue the next PR
        for pr in self:
            _logger.info('Checking if forward-port %s (%s)', pr, pr.number)
            if not pr.parent_id or pr.state not in ('validated', 'ready'):
                _logger.info('-> no parent or wrong state (%s)', pr.state)
                continue
            # if it already has a child, bail
            if self.search_count([('parent_id', '=', self.id)]):
                _logger.info('-> already has a child')
                continue

            # check if we've already selected this PR's batch for forward
            # porting and just haven't come around to it yet
            batch = pr.batch_id
            _logger.info("%s %s %s", pr, batch, batch.prs)
            if self.env['forwardport.batches'].search_count([('batch_id', '=', batch.id)]):
                _logger.warn('-> already recorded')
                continue

            # check if batch-mate are all valid
            mates = batch.prs
            # wait until all of them are validated or ready
            if any(pr.state not in ('validated', 'ready') for pr in mates):
                _logger.warn("-> not ready (%s)", [(pr.number, pr.state) for pr in mates])
                continue

            # check that there's no weird-ass state
            if not all(pr.parent_id for pr in mates):
                _logger.warn("Found a batch (%s) with only some PRs having parents, ignoring", mates)
                continue
            if self.search_count([('parent_id', 'in', mates.ids)]):
                _logger.warn("Found a batch (%s) with only some of the PRs having children", mates)
                continue

            _logger.info('-> ok')
            self.env['forwardport.batches'].create({
                'batch_id': batch.id,
                'source': 'fp',
            })

    def _forward_port_sequence(self):
        # risk: we assume disabled branches are still at the correct location
        # in the FP sequence (in case a PR was merged to a branch which is now
        # disabled, could happen right around the end of the support window)
        # (or maybe we can ignore this entirely and assume all relevant
        # branches are active?)
        fp_complete = self.env['runbot_merge.branch'].with_context(active_test=False)._forward_port_ordered()
        candidates = iter(fp_complete)
        for b in candidates:
            if b == self.target:
                break
        # the candidates iterator is just past the current branch's target
        for target in candidates:
            if target.fp_enabled:
                yield target
            if target == self.limit_id:
                break

    def _find_next_target(self, reference):
        """ Finds the branch between target and limit_id which follows
        reference
        """
        if reference.target == self.limit_id:
            return
        # NOTE: assumes even disabled branches are properly sequenced, would
        #       probably be a good idea to have the FP view show all branches
        branches = list(self.env['runbot_merge.branch'].with_context(active_test=False)._forward_port_ordered())

        # get all branches between max(root.target, ref.target) (excluded) and limit (included)
        from_ = max(branches.index(self.target), branches.index(reference.target))
        to_ = branches.index(self.limit_id)

        # return the first active branch in the set
        return next((
            branch
            for branch in branches[from_+1:to_+1]
            if branch.fp_enabled
        ), None)

    def _iter_descendants(self):
        pr = self
        while True:
            pr = self.search([('parent_id', '=', pr.id)])
            if pr:
                yield pr
            else:
                break

    def _iter_ancestors(self):
        while self:
            yield self
            self = self.parent_id

    def _get_root(self):
        root = self
        while root.parent_id:
            root = root.parent_id
        return root

    def _port_forward(self):
        if not self:
            return

        ref = self[0]

        base = ref.source_id or ref._get_root()
        target = base._find_next_target(ref)
        if target is None:
            _logger.info(
                "Will not forward-port %s#%s: no next target",
                ref.repository.name, ref.number,
            )
            return  # QUESTION: do the prs need to be updated?

        proj = self.mapped('target.project_id')
        if not proj.fp_github_token:
            _logger.warn(
                "Can not forward-port %s#%s: no token on project %s",
                ref.repository.name, ref.number,
                proj.name
            )
            return

        notarget = [p.repository.name for p in self if not p.repository.fp_remote_target]
        if notarget:
            _logger.warn(
                "Can not forward-port %s: repos %s don't have a remote configured",
                self, ', '.join(notarget)
            )
            return

        # take only the branch bit
        new_branch = '%s-%s-%s-forwardport' % (
            target.name,
            base.refname,
            # avoid collisions between fp branches (labels can be reused
            # or conflict especially as we're chopping off the owner)
            base64.b32encode(os.urandom(5)).decode()
        )
        # TODO: send outputs to logging?
        conflicts = {}
        with contextlib.ExitStack() as s:
            for pr in self:
                conflicts[pr], working_copy = pr._create_fp_branch(
                    target, new_branch, s)

                working_copy.push('target', new_branch)

        has_conflicts = any(conflicts.values())
        # problemo: this should forward port a batch at a time, if porting
        # one of the PRs in the batch fails is huge problem, though this loop
        # only concerns itself with the creation of the followup objects so...
        new_batch = self.browse(())
        for pr in self:
            owner, _ = pr.repository.fp_remote_target.split('/', 1)
            source = pr.source_id or pr
            message = source.message
            (h, out, err) = conflicts.get(pr) or (None, None, None)
            if h:
                message = """Cherrypicking %s of source #%d failed with the following

stdout:
```
%s
```

stderr:
```
%s
```

Either perform the forward-port manually (and push to this branch, proceeding
as usual) or close this PR (maybe?).

In the former case, you may want to edit this PR message as well.
""" % (h, source.number, out, err)

            r = requests.post(
                'https://api.github.com/repos/{}/pulls'.format(pr.repository.name), json={
                    'title': "Forward Port of #%d to %s%s" % (
                        source.number,
                        target.name,
                        ' (failed)' if has_conflicts else ''
                    ),
                    'message': message,
                    'head': '%s:%s' % (owner, new_branch),
                    'base': target.name,
                    'draft': has_conflicts,
                }, headers={
                    'Accept': 'application/vnd.github.shadow-cat-preview+json',
                    'Authorization': 'token %s' % pr.repository.project_id.fp_github_token,
                }
            )
            assert 200 <= r.status_code < 300, r.json()
            new_pr = self._from_gh(r.json())
            _logger.info("Created forward-port PR %s", new_pr)
            new_batch |= new_pr

            new_pr.write({
                'merge_method': pr.merge_method,
                'source_id': source.id,
                # only link to previous PR of sequence if cherrypick passed
                'parent_id': pr.id if not has_conflicts else False,
            })
            assignees = (new_pr.source_id.author | new_pr.source_id.reviewed_by).mapped('github_login')
            self.env['runbot_merge.pull_requests.feedback'].create({
                'repository': new_pr.repository.id,
                'pull_request': new_pr.number,
                'message': "Ping %s" % ', '.join('@' + login for login in assignees if login),
            })
            # not great but we probably want to avoid the risk of the webhook
            # creating the PR from under us. There's still a "hole" between
            # the POST being executed on gh and the commit but...
            self.env.cr.commit()

        # batch the PRs so _validate can perform the followup FP properly
        # (with the entire batch). If there are conflict then create a
        # deactivated batch so the interface is coherent but we don't pickup
        # an active batch we're never going to deactivate.
        return self.env['runbot_merge.batch'].create({
            'target': target.id,
            'prs': [(6, 0, new_batch.ids)],
            'active': not has_conflicts,
        })

    def _create_fp_branch(self, target_branch, fp_branch_name, cleanup):
        """ Creates a forward-port for the current PR to ``target_branch`` under
        ``fp_branch_name``.

        :param target_branch: the branch to port forward to
        :param fp_branch_name: the name of the branch to create the FP under
        :param ExitStack cleanup: so the working directories can be cleaned up
        :return: (conflictp, working_copy)
        :rtype: (bool, Repo)
        """
        source = self._get_local_directory()
        # update all the branches & PRs
        source.with_params('gc.pruneExpire=1.day.ago').fetch('-p', 'origin')
        # FIXME: check that pr.head is pull/{number}'s head instead?
        source.cat_file(e=self.head)
        # create working copy
        working_copy = source.clone(
            cleanup.enter_context(tempfile.TemporaryDirectory()),
            branch=target_branch.name
        )
        project_id = self.repository.project_id
        # configure local repo so commits automatically pickup bot identity
        working_copy.config('--local', 'user.name', project_id.fp_github_name)
        working_copy.config('--local', 'user.email', project_id.fp_github_email)
        # add target remote
        working_copy.remote(
            'add', 'target',
            'https://{p.fp_github_name}:{p.fp_github_token}@github.com/{r.fp_remote_target}'.format(
                r=self.repository,
                p=project_id
            )
        )
        working_copy.checkout(b=fp_branch_name)

        root = self._get_root()
        try:
            root._cherry_pick(working_copy)
        except CherrypickError as e:
            # apply the diff of the PR to the target
            # in git diff, "A...B" selects the bits of B not in A
            # (which is the other way around from gitrevisions(7)
            # because git)
            diff = working_copy.stdout().diff('%s...%s' % root._reference_branches()).stdout
            # apply probably fails (because conflict, we don't care
            working_copy.with_config(input=diff).apply('-3', '-')

            working_copy.commit(
                a=True, allow_empty=True,
                message="""Cherry pick of %s failed

stdout:
%s
stderr:
%s
""" % e.args)
            return e.args, working_copy
        else:
            return None, working_copy

    def _cherry_pick(self, working_copy):
        """ Cherrypicks ``self`` into the working copy

        :return: ``True`` if the cherrypick was successful, ``False`` otherwise
        """
        # <xxx>.cherrypick.<number>
        logger = _logger.getChild('cherrypick').getChild(str(self.number))
        cmap = json.loads(self.commits_map)

        # original head so we can reset
        h = working_copy.stdout().rev_parse('HEAD').stdout

        commits_range = self._reference_branches()
        # need to cherry-pick commit by commit because `-n` doesn't yield
        # back control on each op. `-e` with VISUAL=false kinda works but
        # doesn't seem to provide a good way to know when we're done
        # cherrypicking
        rev_list = working_copy.lazy().stdout()\
            .rev_list('--reverse', '%s..%s' % commits_range)
        commits = list(rev_list.stdout)
        logger.info("%d commit(s) on %s", len(commits), h.decode())
        for commit in commits:
            commit = commit.decode().strip()
            r = working_copy.with_config(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            ).cherry_pick(commit)

            if r.returncode: # pick failed, reset and bail
                logger.info("%s: failed", commit)
                working_copy.reset('--hard', h.decode().strip())
                raise CherrypickError(commit, r.stdout.decode(), r.stderr.decode())

            original_msg = working_copy.stdout().show('-s', '--format=%B', commit).stdout.decode()

            msg = self._parse_commit_message(original_msg)

            # original signed-off-er should be retained but didn't necessarily
            # sign off here, so convert signed-off-by to something else
            sob = msg.headers.getlist('signed-off-by')
            if sob:
                msg.headers.remove('signed-off-by')
                msg.headers.extend(
                    ('original-signed-off-by', v)
                    for v in sob
                )
            # write the *merged* commit as "original", not the PR's
            msg.headers['x-original-commit'] = cmap.get(commit, commit)

            # replace existing commit message with massaged one
            working_copy\
                .with_config(input=str(msg).encode())\
                .commit(amend=True, file='-')
            new = working_copy.stdout().rev_parse('HEAD').stdout.decode()
            logger.info('%s: success -> %s', commit, new)

    def _reference_branches(self):
        """
        :returns: (PR target, PR branch)
        """
        ancestor_branch = 'origin/pull/%d' % self.number
        source_target = 'origin/%s' % self.target.name
        return source_target, ancestor_branch

    def _get_local_directory(self):
        repos_dir = pathlib.Path(user_cache_dir('forwardport'))
        repos_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = repos_dir / self.repository.name

        if repo_dir.is_dir():
            return git(repo_dir)
        else:
            subprocess.run([
                'git', 'clone', '--bare',
                'https://{}:{}@github.com/{}'.format(
                    self.repository.project_id.fp_github_name,
                    self.repository.project_id.fp_github_token,
                    self.repository.name,
                ),
                str(repo_dir)
            ], check=True)
            # add PR branches as local but namespaced (?)
            repo = git(repo_dir)
            repo.config('--add', 'remote.origin.fetch', '+refs/pull/*/head:refs/heads/pull/*')
            return repo

class Stagings(models.Model):
    _inherit = 'runbot_merge.stagings'

    def write(self, vals):
        r = super().write(vals)
        if vals.get('active') == False and self.state == 'success':
            for b in self.with_context(active_test=False).batch_ids:
                # if batch PRs have parents they're part of an FP sequence and
                # thus handled separately
                if not b.mapped('prs.parent_id'):
                    self.env['forwardport.batches'].create({
                        'batch_id': b.id,
                        'source': 'merge',
                    })
        return r


def git(directory): return Repo(directory, check=True)
class Repo:
    def __init__(self, directory, **config):
        self._directory = str(directory)
        self._config = config
        self._params = ()
        self._opener = subprocess.run

    def __getattr__(self, name):
        return GitCommand(self, name.replace('_', '-'))

    def _run(self, *args, **kwargs):
        opts = {**self._config, **kwargs}
        return self._opener(
            ('git', '-C', self._directory)
            + tuple(itertools.chain.from_iterable(('-c', p) for p in self._params))
            + args,
            **opts
        )

    def stdout(self, flag=True):
        if flag is True:
            return self.with_config(stdout=subprocess.PIPE)
        elif flag is False:
            return self.with_config(stdout=None)
        return self.with_config(stdout=flag)

    def lazy(self):
        r = self.with_config()
        r._config.pop('check', None)
        r._opener = subprocess.Popen
        return r

    def check(self, flag):
        return self.with_config(check=flag)

    def with_config(self, **kw):
        opts = {**self._config, **kw}
        r = Repo(self._directory, **opts)
        r._opener = self._opener
        r._params = self._params
        return r

    def with_params(self, *args):
        r = self.with_config()
        r._params = args
        return r

    def clone(self, to, branch=None):
        self._run(
            'git', 'clone', '-s',
            *([] if branch is None else ['-b', branch]),
            self._directory, to,
        )
        return Repo(to)

class GitCommand:
    def __init__(self, repo, name):
        self._name = name
        self._repo = repo

    def __call__(self, *args, **kwargs):
        return self._repo._run(self._name, *args, *self._to_options(kwargs))

    def _to_options(self, d):
        for k, v in d.items():
            if len(k) == 1:
                yield '-' + k
            else:
                yield '--' + k.replace('_', '-')
            if v not in (None, True):
                assert v is not False
                yield str(v)

class CherrypickError(Exception):
    ...
