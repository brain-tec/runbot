# -*- coding: utf-8 -*-
import datetime
import dateutil
import json
import logging
import random
import re
import requests
import signal
import subprocess
import time
import glob
import shutil

from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import DEFAULT_SERVER_DATETIME_FORMAT
from odoo import models, fields, api, registry
from odoo.modules.module import get_module_resource
from odoo.tools import config
from odoo.osv import expression
from ..common import fqdn, dt2time, dest_reg, os
from ..container import docker_ps, docker_stop
from psycopg2.extensions import TransactionRollbackError

_logger = logging.getLogger(__name__)


# WHAT IF push 12.0/13.0/13.4 in odoo-dev?

class RunbotException(Exception):
    pass


class RepoTrigger(models.Model):
    """
    List of repo parts that must be part of the same bundle
    """

    _name = 'runbot.trigger'
    _inherit = 'mail.thread'
    _description = 'Triggers'

    name = fields.Char("Repo trigger descriptions")
    project_id = fields.Many2one('runbot.project', required=True)  # main/security/runbot
    repos_ids = fields.Many2many('runbot.repo', relation='runbot_trigger_triggers', string="Triggers")
    dependency_ids = fields.Many2many('runbot.repo', relation='runbot_trigger_dependencies', string="Dependencies")
    config_id = fields.Many2one('runbot.build.config', 'Config')

    # TODO add repo_module_id to replace modules_auto (many2many or option to only use trigger)


class Remote(models.Model):
    """
    Regroups repo and it duplicates (forks): odoo+odoo-dev for each repo
    """
    _name = 'runbot.remote'
    _description = 'Remote'
    _order = 'sequence, id'

    name = fields.Char('Repository', required=True)
    sequence = fields.Integer('Sequence')
    repo_id = fields.Many2one('runbot.repo', required=True)
    short_name = fields.Char('Short name', compute='_compute_short_name')
    remote_name = fields.Char('Remote name', compute='_compute_remote_name')
    base = fields.Char(compute='_get_base_url', string='Base URL', readonly=True)  # Could be renamed to a more explicit name like base_url
    fetch_pull = fields.Boolean('Fetch PR', default=True)
    fetch_heads = fields.Boolean('Fetch branches', default=True)
    token = fields.Char("Github token", groups="runbot.group_runbot_admin")

    @api.model
    def _sanitized_name(self, name):
        for i in '@:/':
            name = name.replace(i, '_')
        return name

    @api.depends('name')
    def _get_base_url(self):
        for remote in self:
            name = re.sub('.+@', '', remote.name)
            name = re.sub('^https://', '', name)  # support https repo style
            name = re.sub('.git$', '', name)
            name = name.replace(':', '/')
            remote.base = name

    @api.depends('name', 'base')
    def _compute_short_name(self):
        for remote in self:
            remote.short_name = '/'.join(remote.base.split('/')[-2:])

    def _compute_remote_name(self):
        for remote in self:
            remote.remote_name = self._sanitized_name(remote.short_name)

    def _github(self, url, payload=None, ignore_errors=False, nb_tries=2):
        """Return a http request to be sent to github"""
        for remote in self:
            if not remote.token:
                return
            match_object = re.search('([^/]+)/([^/]+)/([^/.]+(.git)?)', remote.base)
            if match_object:
                url = url.replace(':owner', match_object.group(2))
                url = url.replace(':repo', match_object.group(3))
                url = 'https://api.%s%s' % (match_object.group(1), url)
                session = requests.Session()
                session.auth = (remote.token, 'x-oauth-basic')
                session.headers.update({'Accept': 'application/vnd.github.she-hulk-preview+json'})
                try_count = 0
                while try_count < nb_tries:
                    try:
                        if payload:
                            response = session.post(url, data=json.dumps(payload))
                        else:
                            response = session.get(url)
                        response.raise_for_status()
                        if try_count > 0:
                            _logger.info('Success after %s tries' % (try_count + 1))
                        return response.json()
                    except Exception as e:
                        try_count += 1
                        if try_count < nb_tries:
                            time.sleep(2)
                        else:
                            if ignore_errors:
                                _logger.exception('Ignored github error %s %r (try %s/%s)' % (url, payload, try_count + 1, nb_tries))
                            else:
                                raise

class Repo(models.Model):

    _name = "runbot.repo"
    _description = "Repo"
    _order = 'sequence, id'


    name = fields.Char("Repo forks descriptions", unique=True)  # odoo/enterprise/upgrade/security/runbot/design_theme
    main_remote = fields.Many2one('runbot.remote', "Main remote")
    remote_ids = fields.One2many('runbot.remote', 'repo_id', "Repo and forks")
    project_id = fields.Many2one('runbot.project', required=True,
        help="Default bundle project to use when pushing on this repos")
    # -> not verry usefull, remove it? (iterate on projects or contraints triggers:
    # all trigger where a repo is used must be in the same project.
    modules = fields.Char("Modules to install", help="Comma-separated list of modules to install and test.")
    group_ids = fields.Many2many('res.groups', string='Limited to groups')
    server_files = fields.Char('Server files', help='Comma separated list of possible server files')  # odoo-bin,openerp-server,openerp-server.py
    manifest_files = fields.Char('Manifest files', help='Comma separated list of possible manifest files', default='__manifest__.py')
    addons_paths = fields.Char('Addons paths', help='Comma separated list of possible addons path', default='')

    sequence = fields.Integer('Sequence')
    path = fields.Char(compute='_get_path', string='Directory', readonly=True)
    mode = fields.Selection([('disabled', 'Disabled'),
                             ('poll', 'Poll'),
                             ('hook', 'Hook')],
                            default='poll',
                            string="Mode", required=True, help="hook: Wait for webhook on /runbot/hook/<id> i.e. github push event")
    hook_time = fields.Float('Last hook time', compute='_compute_hook_time')
    get_ref_time = fields.Float('Last refs db update', compute='_compute_get_ref_time')

    def _compute_get_ref_time(self):
        self.env.cr.execute("""
            SELECT repo_id, time FROM runbot_repo_reftime
            WHERE id IN (
                SELECT max(id) FROM runbot_repo_reftime 
                WHERE repo_id = any(%s) GROUP BY repo_id
            )
        """, [self.ids])
        times = dict(self.env.cr.fetchall())
        for repo in self:
            repo.get_ref_time = times.get(repo.id, 0)

    def _compute_hook_time(self):
        self.env.cr.execute("""
            SELECT repo_id, time FROM runbot_repo_hooktime
            WHERE id IN (
                SELECT max(id) FROM runbot_repo_hooktime 
                WHERE repo_id = any(%s) GROUP BY repo_id
            )
        """, [self.ids])
        times = dict(self.env.cr.fetchall())

        for repo in self:
            repo.hook_time = times.get(repo.id, 0)

    def set_hook_time(self, value):
        for repo in self:
            self.env['runbot.repo.hooktime'].create({'time': value, 'repo_id': repo.id})
        self.invalidate_cache()

    def set_ref_time(self, value):
        for repo in self:
            self.env['runbot.repo.reftime'].create({'time': value, 'repo_id': repo.id})
        self.invalidate_cache()

    def _gc_times(self):
        self.env.cr.execute("""
            DELETE from runbot_repo_reftime WHERE id NOT IN (
                SELECT max(id) FROM runbot_repo_reftime GROUP BY repo_id
            )
        """)
        self.env.cr.execute("""
            DELETE from runbot_repo_hooktime WHERE id NOT IN (
                SELECT max(id) FROM runbot_repo_hooktime GROUP BY repo_id
            )
        """)

    def _root(self):
        """Return root directory of repository"""
        default = os.path.join(os.path.dirname(__file__), '../static')
        return os.path.abspath(default)

    def _source_path(self, sha, *path):
        """
        returns the absolute path to the source folder of the repo (adding option *path)
        """
        self.ensure_one()
        return os.path.join(self._root(), 'sources', self.name, sha, *path)

    @api.depends('name')
    def _get_path(self):
        """compute the server path of repo from the name"""
        root = self._root()
        for repo in self:
            repo.path = os.path.join(root, 'repo', repo._sanitized_name(repo.name))

    def _git(self, cmd):
        """Execute a git command 'cmd'"""
        self.ensure_one()
        _logger.debug("git command: git (dir %s) %s", self.short_name, ' '.join(cmd))
        cmd = ['git', '--git-dir=%s' % self.path] + cmd
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()

    def _git_rev_parse(self, branch_name):
        return self._git(['rev-parse', branch_name]).strip()

    def _git_export(self, sha):
        """Export a git repo into a sources"""
        # TODO add automated tests
        self.ensure_one()
        export_path = self._source_path(sha)

        if os.path.isdir(export_path):
            _logger.info('git export: checkouting to %s (already exists)' % export_path)
            return export_path

        if not self._hash_exists(sha):
            self._update(force=True)
            if not self._hash_exists(sha):
                try:
                    result = self._git(['fetch', 'all', sha])
                except:
                    pass
                if not self._hash_exists(sha):
                    raise RunbotException("Commit %s is unreachable. Did you force push the branch since build creation?" % sha)

        _logger.info('git export: checkouting to %s (new)' % export_path)
        os.makedirs(export_path)

        p1 = subprocess.Popen(['git', '--git-dir=%s' % self.path, 'archive', sha], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['tar', '-xmC', export_path], stdin=p1.stdout, stdout=subprocess.PIPE)
        p1.stdout.close()  # Allow p1 to receive a SIGPIPE if p2 exits.
        (out, err) = p2.communicate()
        if err:
            raise RunbotException("Archive %s failed. Did you force push the branch since build creation? (%s)" % (sha, err))

        # migration scripts link if necessary
        icp = self.env['ir.config_parameter']
        ln_param = icp.get_param('runbot_migration_ln', default='')
        migration_repo_id = int(icp.get_param('runbot_migration_repo_id', default=0))
        # TODO check that
        if ln_param and migration_repo_id and self.server_files:
            scripts_dir = self.env['runbot.repo'].browse(migration_repo_id).name
            try:
                os.symlink('/data/build/%s' % scripts_dir,  self._source_path(sha, ln_param))
            except FileNotFoundError:
                _logger.warning('Impossible to create migration symlink')

        # TODO get result and fallback on cleaing in case of problem
        return export_path

    def _hash_exists(self, commit_hash):
        """ Verify that a commit hash exists in the repo """
        self.ensure_one()
        try:
            self._git(['cat-file', '-e', commit_hash])
        except subprocess.CalledProcessError:
            return False
        return True

    def _get_fetch_head_time(self):
        self.ensure_one()
        fname_fetch_head = os.path.join(self.path, 'FETCH_HEAD')
        if os.path.exists(fname_fetch_head):
            return os.path.getmtime(fname_fetch_head)

    def _get_refs(self):
        """Find new refs
        :return: list of tuples with following refs informations:
        name, sha, date, author, author_email, subject, committer, committer_email
        """
        self.ensure_one()

        get_ref_time = round(self._get_fetch_head_time(), 4)
        if not self.get_ref_time or get_ref_time > self.get_ref_time:
            self.set_ref_time(get_ref_time)
            fields = ['refname', 'objectname', 'committerdate:iso8601', 'authorname', 'authoremail', 'subject', 'committername', 'committeremail']
            fmt = "%00".join(["%(" + field + ")" for field in fields])
            git_refs = self._git(['for-each-ref', '--format', fmt, '--sort=-committerdate', 'refs/heads', 'refs/pull'])
            git_refs = git_refs.strip()
            return [tuple(field for field in line.split('\x00')) for line in git_refs.split('\n')]
        else:
            return []

    def _find_or_create_branches(self, refs):
        """Parse refs and create branches that does not exists yet
        :param refs: list of tuples returned by _get_refs()
        :return: dict {branch.name: branch.id}
        The returned structure contains all the branches from refs newly created
        or older ones.
        """

        # FIXME WIP
        names = [r[0] for r in refs]
        branches = self.env['runbot.branch'].search([('name', 'in', names), ('remote_id', '=', self.id)])
        ref_branches = {branch.name: branch for branch in branches}
        for ref_name, sha, date, author, author_email, subject, committer, committer_email in refs:
            if not ref_branches.get(name):
                # format example:
                # refs/ruodoo-dev/heads/12.0-must-fail
                # refs/ruodoo/pull/1
                _, remote_name, branch_type, name = ref_name.split('/')
                repo_id = self.repo_ids.filtered(lambda r: r.remote_name == remote_name).id
                _logger.debug('repo %s found new branch %s', self.name, name)
                new_branch = self.env['runbot.branch'].create({'repo_id': repo_id, 'name': name, 'is_pr': branch_type == 'pull'})
                ref_branches[ref_name] = new_branch
        return ref_branches

    def _find_new_commits(self, refs, ref_branches):
        """Find new commits in bare repo
        :param refs: list of tuples returned by _get_refs()
        :param ref_branches: dict structure {branch.name: branch.id}
                             described in _find_or_create_branches
        """
        self.ensure_one()
        max_age = int(self.env['ir.config_parameter'].get_param('runbot.runbot_max_age', default=30))

        for ref_name, sha, date, author, author_email, subject, committer, committer_email in refs:
            branch = ref_branches[ref_name]

            # skip the build for old branches (Could be checked before creating the branch in DB ?)
            # if dateutil.parser.parse(date[:19]) + datetime.timedelta(days=max_age) < datetime.datetime.now():
            #     continue
            # create build (and mark previous builds as skipped) if not found
            if branch.head_name != sha: # new push on branch
                _logger.debug('repo %s branch %s new commit found: %s', self.name, branch.name, sha)

                commit = self.env['runbot.commit'].search([('name', '=', sha), ('repo_id', '=', self.id)])
                if not commit:
                    commit = self.env['runbot.commit'].create({
                        'name': sha,
                        'repo_id': self.id,
                        'author': author,
                        'author_email': author_email,
                        'committer': committer,
                        'committer_email': committer_email,
                        'subject': subject,
                        'date': dateutil.parser.parse(date[:19]),
                    })
                branch.head = commit

                bundle = branch.bundle_id
                if bundle.no_build:
                    continue

                # TODO check  if repo group of branch is a trigger
                # todo move following logic to bundle ? bundle._notify_new_commit()
                bundle_batch = bundle._get_preparing_batch()
                bundle_batch._new_commit(commit)
                # TODO add reflog

    def _create_batches(self):
        """ Find new commits in physical repos"""
        refs = {}
        ref_branches = {}
        for repo in self:
            try:
                ref = repo._get_refs()
                max_age = int(self.env['ir.config_parameter'].get_param('runbot.runbot_max_age', default=30))
                good_refs = [r for r in ref if dateutil.parser.parse(r[2][:19]) + datetime.timedelta(days=max_age) > datetime.datetime.now()]
                if good_refs:
                    refs[repo] = good_refs
            except Exception:
                _logger.exception('Fail to get refs for repo %s', repo.name)
            if repo in refs:
                ref_branches[repo] = repo._find_or_create_branches(refs[repo])

        # keep _find_or_create_branches separated from build creation to ease
        # closest branch detection
        # todo, maybe this can be simplified now
        for repo in self:
            if repo in refs:
                repo._find_new_commits(refs[repo], ref_branches[repo])

    def _clone(self):
        """ Clone the remote repo if needed """
        self.ensure_one()
        repo = self
        if not os.path.isdir(os.path.join(repo.path, 'refs')):
            _logger.info("Cloning repository '%s' in '%s'" % (repo.name, repo.path))
            subprocess.call(['git', 'clone', '--bare', repo.name, repo.path])
            # TODO bare init and add remote per repo
            # TODO bare update remotes on write/create
            # -> maybe the easiest solution would be to create a qweb view to write on config.
            # -> configure by repo what to fecth and add this to remote config
            # example: 
            # [core]
            #     repositoryformatversion = 0
            #     filemode = true
            #     bare = true
            # [remote "ruodoo"]
            #     url = git@github.com:ruodoo/odoo.git
            #     fetch = +refs/heads/*:refs/remotes/ruodoo/*
            #     fetch = +refs/pull/*/head:refs/remotes/ruodoo-dev/pr/*
            # [remote "ruodoo-dev"]
            #     url = git@github.com:ruodoo-dev/odoo.git
            #     fetch = +refs/heads/*:refs/remotes/ruodoo-dev/*

            # this example will fetch pr on ruodoo/odoo but not on odoo/odoo

    def _update_git(self, force):
        """ Update the git repo on FS """
        self.ensure_one()
        repo = self
        _logger.debug('repo %s updating branches', repo.name)

        if not os.path.isdir(os.path.join(repo.path)):
            os.makedirs(repo.path)
        self._clone()
        # TODO bare check repo in remotes

        # check for mode == hook
        fname_fetch_head = os.path.join(repo.path, 'FETCH_HEAD')
        if not force and os.path.isfile(fname_fetch_head):
            fetch_time = os.path.getmtime(fname_fetch_head)
            if repo.mode == 'hook' and (not repo.hook_time or repo.hook_time < fetch_time):
                t0 = time.time()
                _logger.debug('repo %s skip hook fetch fetch_time: %ss ago hook_time: %ss ago',
                            repo.name, int(t0 - fetch_time), int(t0 - repo.hook_time) if repo.hook_time else 'never')
                return

        self._update_fetch_cmd()

    def _update_fetch_cmd(self):
        # Extracted from update_git to be easily overriden in external module
        self.ensure_one()
        repo = self
        try_count = 0
        failure = True
        delay = 0

        while failure and try_count < 5:
            time.sleep(delay)
            try:
                repo._git(['fetch', '-p', 'origin', '+refs/heads/*:refs/heads/*', '+refs/pull/*/head:refs/pull/*'])
                failure = False
            except subprocess.CalledProcessError as e:
                try_count += 1
                delay = delay * 1.5 if delay else 0.5
                if try_count > 4:
                    message = 'Failed to fetch repo %s: %s' % (repo.name, e.output.decode())
                    _logger.exception(message)
                    host = self.env['runbot.host']._get_current()
                    host.disable()

    def _update(self, force=True):
        """ Update the physical git reposotories on FS"""
        for repo in reversed(self):
            try:
                repo._update_git(force) # TODO xdo, check gc log and log warning
            except Exception:
                _logger.exception('Fail to update repo %s', repo.name)

    def _commit(self):
        self.env.cr.commit()
        self.env.cache.invalidate()
        self.env.clear()

    def _scheduler(self, host):
        nb_workers = host.get_nb_worker()

        self._gc_testing(host)
        self._commit()
        for build in self._get_builds_with_requested_actions(host):
            build._process_requested_actions()
            self._commit()
        for build in self._get_builds_to_schedule(host):
            build._schedule()
            self._commit()
        self._assign_pending_builds(host, nb_workers, [('build_type', '!=', 'scheduled')])
        self._commit()
        self._assign_pending_builds(host, nb_workers-1 or nb_workers)
        self._commit()
        for build in self._get_builds_to_init(host):
            build._init_pendings(host)
            self._commit()
        self._gc_running(host)
        self._commit()
        self._reload_nginx()

    def build_domain_host(self, host, domain=None):
        domain = domain or []
        return [('host', '=', host.name)] + domain

    def _get_builds_with_requested_actions(self, host):
        return self.env['runbot.build'].search(self.build_domain_host(host, [('requested_action', 'in', ['wake_up', 'deathrow'])]))

    def _get_builds_to_schedule(self, host):
        return self.env['runbot.build'].search(self.build_domain_host(host, [('local_state', 'in', ['testing', 'running'])]))

    def _assign_pending_builds(self, host, nb_workers, domain=None):
        if not self.ids or host.assigned_only or nb_workers <= 0:
            return
        domain_host = self.build_domain_host(host)
        reserved_slots = self.env['runbot.build'].search_count(domain_host + [('local_state', 'in', ('testing', 'pending'))])
        assignable_slots = (nb_workers - reserved_slots)
        if assignable_slots > 0:
            allocated = self._allocate_builds(host, assignable_slots, domain)
            if allocated:
                _logger.debug('Builds %s where allocated to runbot' % allocated)

    def _get_builds_to_init(self, host):
        domain_host = self.build_domain_host(host)
        used_slots = self.env['runbot.build'].search_count(domain_host + [('local_state', '=', 'testing')])
        available_slots = host.get_nb_worker() - used_slots
        if available_slots <= 0:
            return self.env['runbot.build']
        return self.env['runbot.build'].search(domain_host + [('local_state', '=', 'pending')], limit=available_slots)

    def _gc_running(self, host):
        running_max = host.get_running_max()
        # terminate and reap doomed build
        domain_host = self.build_domain_host(host)
        Build = self.env['runbot.build']
        # some builds are marked as keep running
        cannot_be_killed_ids = Build.search(domain_host + [('keep_running', '!=', True)]).ids
        # we want to keep one build running per sticky, no mather which host
        sticky_branches_ids = self.env['runbot.branch'].search([('sticky', '=', True)]).ids
        # search builds on host on sticky branches, order by position in branch history
        if sticky_branches_ids:
            self.env.cr.execute("""
                SELECT
                    id
                FROM (
                    SELECT
                        bu.id AS id,
                        bu.host as host,
                        row_number() OVER (PARTITION BY branch_id order by bu.id desc) AS row
                    FROM
                        runbot_branch br INNER JOIN runbot_build bu ON br.id=bu.branch_id
                    WHERE
                        br.id in %s AND (bu.hidden = 'f' OR bu.hidden IS NULL)
                    ) AS br_bu
                WHERE
                    row <= 4 AND host = %s
                ORDER BY row, id desc
                """, [tuple(sticky_branches_ids), host.name]
            )
            cannot_be_killed_ids += self.env.cr.fetchall() #looks wrong?
        cannot_be_killed_ids = cannot_be_killed_ids[:running_max]  # ensure that we don't try to keep more than we can handle

        build_ids = Build.search(domain_host + [('local_state', '=', 'running'), ('id', 'not in', cannot_be_killed_ids)], order='job_start desc').ids
        Build.browse(build_ids)[running_max:]._kill()

    def _gc_testing(self, host):
        """garbage collect builds that could be killed"""
        # decide if we need room
        Build = self.env['runbot.build']
        domain_host = self.build_domain_host(host)
        testing_builds = Build.search(domain_host + [('local_state', 'in', ['testing', 'pending']), ('requested_action', '!=', 'deathrow')])
        used_slots = len(testing_builds)
        available_slots = host.get_nb_worker() - used_slots
        nb_pending = Build.search_count([('local_state', '=', 'pending'), ('host', '=', False)])
        if available_slots > 0 or nb_pending == 0:
            return
        for build in testing_builds:
            top_parent = build._get_top_parent()
            if not build.branch_id.sticky:
                newer_candidates = Build.search([
                    ('id', '>', build.id),
                    ('branch_id', '=', build.branch_id.id),
                    ('build_type', '=', 'normal'),
                    ('parent_id', '=', False),
                    ('hidden', '=', False),
                    ('config_id', '=', top_parent.config_id.id)
                ])
                if newer_candidates:
                    top_parent._ask_kill(message='Build automatically killed, newer build found %s.' % newer_candidates.ids)

    def _allocate_builds(self, host, nb_slots, domain=None):
        if nb_slots <= 0:
            return []
        non_allocated_domain = [('repo_id', 'in', self.ids), ('local_state', '=', 'pending'), ('host', '=', False)]
        if domain:
            non_allocated_domain = expression.AND([non_allocated_domain, domain])
        e = expression.expression(non_allocated_domain, self.env['runbot.build'])
        assert e.get_tables() == ['"runbot_build"']
        where_clause, where_params = e.to_sql()

        # self-assign to be sure that another runbot batch cannot self assign the same builds
        query = """UPDATE
                        runbot_build
                    SET
                        host = %%s
                    WHERE
                        runbot_build.id IN (
                            SELECT runbot_build.id
                            FROM runbot_build
                            LEFT JOIN runbot_branch
                            ON runbot_branch.id = runbot_build.branch_id
                            WHERE
                                %s
                            ORDER BY
                                array_position(array['normal','rebuild','indirect','scheduled']::varchar[], runbot_build.build_type) ASC,
                                runbot_branch.sticky DESC,
                                runbot_branch.priority DESC,
                                runbot_build.sequence ASC
                            FOR UPDATE OF runbot_build SKIP LOCKED
                            LIMIT %%s
                        )
                    RETURNING id""" % where_clause
        self.env.cr.execute(query, [host.name] + where_params + [nb_slots])
        return self.env.cr.fetchall()

    def _domain(self):
        return self.env.get('ir.config_parameter').get_param('runbot.runbot_domain', fqdn())

    def _reload_nginx(self):
        # TODO move this elsewhere
        env = self.env
        settings = {}
        settings['port'] = config.get('http_port')
        settings['runbot_static'] = os.path.join(get_module_resource('runbot', 'static'), '')
        nginx_dir = os.path.join(self._root(), 'nginx')
        settings['nginx_dir'] = nginx_dir
        settings['re_escape'] = re.escape
        settings['fqdn'] = fqdn()

        icp = env['ir.config_parameter'].sudo()
        nginx = icp.get_param('runbot.runbot_nginx', True)  # or just force nginx?

        if nginx:
            settings['builds'] = env['runbot.build'].search([('local_state', '=', 'running'), ('host', '=', fqdn())])

            nginx_config = env['ir.ui.view'].render_template("runbot.nginx_config", settings)
            os.makedirs(nginx_dir, exist_ok=True)
            content = None
            nginx_conf_path = os.path.join(nginx_dir, 'nginx.conf')
            content = ''
            if os.path.isfile(nginx_conf_path):
                with open(nginx_conf_path, 'rb') as f:
                    content = f.read()
            if content != nginx_config:
                _logger.debug('reload nginx')
                with open(nginx_conf_path, 'wb') as f:
                    f.write(nginx_config)
                try:
                    pid = int(open(os.path.join(nginx_dir, 'nginx.pid')).read().strip(' \n'))
                    os.kill(pid, signal.SIGHUP)
                except Exception:
                    _logger.debug('start nginx')
                    if subprocess.call(['/usr/sbin/nginx', '-p', nginx_dir, '-c', 'nginx.conf']):
                        # obscure nginx bug leaving orphan worker listening on nginx port
                        if not subprocess.call(['pkill', '-f', '-P1', 'nginx: worker']):
                            _logger.debug('failed to start nginx - orphan worker killed, retrying')
                            subprocess.call(['/usr/sbin/nginx', '-p', nginx_dir, '-c', 'nginx.conf'])
                        else:
                            _logger.debug('failed to start nginx - failed to kill orphan worker - oh well')

    def _get_cron_period(self, min_margin=120):
        """ Compute a randomized cron period with a 2 min margin below
        real cron timeout from config.
        """
        cron_limit = config.get('limit_time_real_cron')
        req_limit = config.get('limit_time_real')
        cron_timeout = cron_limit if cron_limit > -1 else req_limit
        return cron_timeout - (min_margin + random.randint(1, 60))

    def _cron_fetch_and_schedule(self, hostname):
        """This method have to be called from a dedicated cron on a runbot
        in charge of orchestration.
        """

        if hostname != fqdn():
            return 'Not for me'

        start_time = time.time()
        timeout = self._get_cron_period()
        icp = self.env['ir.config_parameter']
        update_frequency = int(icp.get_param('runbot.runbot_update_frequency', default=10))
        while time.time() - start_time < timeout:
            repos = self.search([('mode', '!=', 'disabled')])
            repos._update(force=False)
            repos._create_batches()
            self._commit()
            time.sleep(update_frequency)

    def _cron_fetch_and_build(self, hostname):
        """ This method have to be called from a dedicated cron
        created on each runbot batch.
        """

        if hostname != fqdn():
            return 'Not for me'

        host = self.env['runbot.host']._get_current()
        host.set_psql_conn_count()
        host._bootstrap()
        host.last_start_loop = fields.Datetime.now()
        
        self._commit()
        start_time = time.time()
        # 1. source cleanup
        # -> Remove sources when no build is using them
        # (could be usefull to keep them for wakeup but we can checkout them again if not forced push)
        self.env['runbot.repo']._source_cleanup()
        # 2. db and log cleanup
        # -> Keep them as long as possible
        self.env['runbot.build']._local_cleanup()
        # 3. docker cleanup
        self.env['runbot.repo']._docker_cleanup()
        host._docker_build()

        timeout = self._get_cron_period()
        icp = self.env['ir.config_parameter']
        update_frequency = int(icp.get_param('runbot.runbot_update_frequency', default=10))
        while time.time() - start_time < timeout:
            time.sleep(self._scheduler_loop_turn(host, update_frequency))

        host.last_end_loop = fields.Datetime.now()

    def _scheduler_loop_turn(self, host, default_sleep=1):
        repos = self.search([('mode', '!=', 'disabled')])
        try:
            repos._scheduler(host)
            host.last_success = fields.Datetime.now()
            self._commit()
        except Exception as e:
            self.env.cr.rollback()
            self.env.clear()
            _logger.exception(e)
            message = str(e)
            if host.last_exception == message:
                host.exception_count += 1
            else:
                host.last_exception = str(e)
                host.exception_count = 1
            self._commit()
            return random.uniform(0, 3)
        else:
            if host.last_exception:
                host.last_exception = ""
                host.exception_count = 0
            return default_sleep

    def _source_cleanup(self):
        try:
            if self.pool._init:
                return
            _logger.info('Source cleaning')
            # we can remove a source only if no build are using them as name or rependency_ids aka as commit
            cannot_be_deleted_builds = self.env['runbot.build'].search([('host', '=', fqdn()), ('local_state', 'not in', ('done', 'duplicate'))])
            cannot_be_deleted_path = set()
            for build in cannot_be_deleted_builds:
                for commit in build.build_commit_ids:
                    cannot_be_deleted_path.add(commit._source_path())

            to_delete = set()
            to_keep = set()
            repos = self.search([('mode', '!=', 'disabled')])
            for repo in repos:
                repo_source = os.path.join(repo._root(), 'sources', repo.name, '*') # TODO check source folder
                for source_dir in glob.glob(repo_source):
                    if source_dir not in cannot_be_deleted_path:
                        to_delete.add(source_dir)
                    else:
                        to_keep.add(source_dir)

            # we are comparing cannot_be_deleted_path with to keep to sensure that the algorithm is working, we want to avoid to erase file by mistake
            # note: it is possible that a parent_build is in testing without checkouting sources, but it should be exceptions
            if to_delete:
                if cannot_be_deleted_path != to_keep:
                    _logger.warning('Inconsistency between sources and database: \n%s \n%s' % (cannot_be_deleted_path-to_keep, to_keep-cannot_be_deleted_path))
                to_delete = list(to_delete)
                to_keep = list(to_keep)
                cannot_be_deleted_path = list(cannot_be_deleted_path)
                for source_dir in to_delete:
                    _logger.info('Deleting source: %s' % source_dir)
                    assert 'static' in source_dir
                    shutil.rmtree(source_dir)
                _logger.info('%s/%s source folder where deleted (%s kept)' % (len(to_delete), len(to_delete+to_keep), len(to_keep)))
        except:
            _logger.error('An exception occured while cleaning sources')
            pass

    def _docker_cleanup(self):
        _logger.info('Docker cleaning')
        docker_ps_result = docker_ps()
        containers = {int(dc.split('-', 1)[0]):dc for dc in docker_ps_result if dest_reg.match(dc)}
        if containers:
            candidates = self.env['runbot.build'].search([('id', 'in', list(containers.keys())), ('local_state', '=', 'done')])
            for c in candidates:
                _logger.info('container %s found running with build state done', containers[c.id])
                docker_stop(containers[c.id], c._path())
        ignored = {dc for dc in docker_ps_result if not dest_reg.match(dc)}
        if ignored:
            _logger.debug('docker (%s) not deleted because not dest format', " ".join(list(ignored)))


class RefTime(models.Model):
    _name = "runbot.repo.reftime"
    _description = "Repo reftime"
    _log_access = False

    time = fields.Float('Time', index=True, required=True)
    repo_id = fields.Many2one('runbot.repo', 'Repository', required=True, ondelete='cascade')


class HookTime(models.Model):
    _name = "runbot.repo.hooktime"
    _description = "Repo hooktime"
    _log_access = False

    time = fields.Float('Time')
    repo_id = fields.Many2one('runbot.repo', 'Repository', required=True, ondelete='cascade')
