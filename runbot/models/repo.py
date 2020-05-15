# -*- coding: utf-8 -*-
import datetime
import json
import logging
import re
import subprocess
import time

import dateutil
import requests

from odoo import models, fields, api
from ..common import os, RunbotException

_logger = logging.getLogger(__name__)


def _sanitize(name):
    for i in '@:/':
        name = name.replace(i, '_')
    return name


class RepoTrigger(models.Model):
    """
    List of repo parts that must be part of the same bundle
    """

    _name = 'runbot.trigger'
    _inherit = 'mail.thread'
    _description = 'Triggers'

    name = fields.Char("Repo trigger descriptions")
    project_id = fields.Many2one('runbot.project', string="Project id", required=True)  # main/security/runbot
    repo_ids = fields.Many2many('runbot.repo', relation='runbot_trigger_triggers', string="Triggers", domain="[('project_id', '=', project_id)]")
    dependency_ids = fields.Many2many('runbot.repo', relation='runbot_trigger_dependencies', string="Dependencies")
    config_id = fields.Many2one('runbot.build.config', string="Config")
    ci_context = fields.Char("Ci context", default='ci/runbot')
    category_id = fields.Many2one('runbot.trigger.category', default=lambda self: self.env.ref('runbot.default_category', raise_if_not_found=False))


class Category(models.Model):
    _name = 'runbot.trigger.category'
    _description = 'Trigger category'

    name = fields.Char("Name")
    icon = fields.Char("Font awesome icon")
    # TODO add a view?


class Remote(models.Model):
    """
    Regroups repo and it duplicates (forks): odoo+odoo-dev for each repo
    """
    _name = 'runbot.remote'
    _description = 'Remote'
    _order = 'sequence, id'

    name = fields.Char('Url', required=True)  # TODO valide with regex
    repo_id = fields.Many2one('runbot.repo', required=True)

    base_url = fields.Char(compute='_compute_base_url', string='Base URL', readonly=True)
    short_name = fields.Char('Short name', compute='_compute_short_name')
    remote_name = fields.Char('Remote name', compute='_compute_remote_name')

    sequence = fields.Integer('Sequence')
    fetch_heads = fields.Boolean('Fetch branches', default=True)
    fetch_pull = fields.Boolean('Fetch PR', default=False)

    token = fields.Char("Github token", groups="runbot.group_runbot_admin")

    @api.depends('name')
    def _compute_base_url(self):
        for remote in self:
            name = re.sub('.+@', '', remote.name)
            name = re.sub('^https://', '', name)  # support https repo style
            name = re.sub('.git$', '', name)
            name = name.replace(':', '/')
            remote.base_url = name

    @api.depends('name', 'base_url')
    def _compute_short_name(self):
        for remote in self:
            remote.short_name = '/'.join(remote.base_url.split('/')[-2:])

    def _compute_remote_name(self):
        for remote in self:
            remote.remote_name = _sanitize(remote.short_name)

    def create(self, values_list):
        remote = super().create(values_list)
        self._cr.after('commit', self.repo_id._update_git_config)
        return remote

    def write(self, values):
        res = super().write(values)
        self._cr.after('commit', self.repo_id._update_git_config)
        return res

    def _github(self, url, payload=None, ignore_errors=False, nb_tries=2):
        """Return a http request to be sent to github"""
        for remote in self:
            match_object = re.search('([^/]+)/([^/]+)/([^/.]+(.git)?)', remote.base_url)
            if match_object:

                url = url.replace(':owner', match_object.group(2))
                url = url.replace(':repo', match_object.group(3))
                url = 'https://api.%s%s' % (match_object.group(1), url)
                session = requests.Session()
                if remote.token:
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
                            _logger.info('Success after %s tries', (try_count + 1))
                        return response.json()
                    except requests.HTTPError:
                        try_count += 1
                        if try_count < nb_tries:
                            time.sleep(2)
                        else:
                            if ignore_errors:
                                _logger.exception('Ignored github error %s %r (try %s/%s)', url, payload, try_count, nb_tries)
                            else:
                                raise
            else:
                _logger.error('Invalid url %s for github_status', remote.base_url)


class Repo(models.Model):

    _name = "runbot.repo"
    _description = "Repo"
    _order = 'sequence, id'


    name = fields.Char("Name", unique=True)  # odoo/enterprise/upgrade/security/runbot/design_theme
    main_remote = fields.Many2one('runbot.remote', "Main remote")
    remote_ids = fields.One2many('runbot.remote', 'repo_id', "Remotes")
    project_id = fields.Many2one('runbot.project', required=True,
        help="Default bundle project to use when pushing on this repos",
        default=lambda self: self.env.ref('runbot.main_project', raise_if_not_found=False))
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
    trigger_ids = fields.Many2many('runbot.trigger', relation='runbot_trigger_triggers', readonly=True)

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

    def _source_path(self, sha, *path):
        """
        returns the absolute path to the source folder of the repo (adding option *path)
        """
        self.ensure_one()
        return os.path.join(self.env['runbot.runbot']._root(), 'sources', self.name, sha, *path)

    @api.depends('name')
    def _get_path(self):
        """compute the server path of repo from the name"""
        root = self.env['runbot.runbot']._root()
        for repo in self:
            repo.path = os.path.join(root, 'repo', _sanitize(repo.name))

    def _git(self, cmd):
        """Execute a git command 'cmd'"""
        self.ensure_one()
        cmd = ['git', '-C', self.path] + cmd
        _logger.info("git command: %s", ' '.join(cmd))
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()

    def _git_export(self, sha):
        """Export a git repo into a sources"""
        #  TODO add automated tests
        self.ensure_one()
        export_path = self._source_path(sha)

        if os.path.isdir(export_path):
            _logger.info('git export: exporting to %s (already exists)', export_path)
            return export_path

        if not self._hash_exists(sha):
            self._update(force=True)
            if not self._hash_exists(sha):
                for remote in self.remote_ids:
                    try:
                        self._git(['fetch', remote.remote_name, sha])  # TODO test me
                        _logger.info('Success fetching specific head %s on %s', sha, remote)
                        break
                    except subprocess.CalledProcessError:
                        pass
                if not self._hash_exists(sha):
                    raise RunbotException("Commit %s is unreachable. Did you force push the branch?" % sha)

        _logger.info('git export: exporting to %s (new)', export_path)
        os.makedirs(export_path)

        p1 = subprocess.Popen(['git', '--git-dir=%s' % self.path, 'archive', sha], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['tar', '-xmC', export_path], stdin=p1.stdout, stdout=subprocess.PIPE)
        p1.stdout.close()  # Allow p1 to receive a SIGPIPE if p2 exits.
        (_, err) = p2.communicate()
        if err:
            # TODO FIXME this error is not catched, redirected to syslog.
            raise RunbotException("Export for %s failed. (%s)" % (sha, err))

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
        return 0

    def _get_refs(self, max_age=30):
        """Find new refs
        :return: list of tuples with following refs informations:
        name, sha, date, author, author_email, subject, committer, committer_email
        """
        self.ensure_one()

        get_ref_time = round(self._get_fetch_head_time(), 4)
        if not self.get_ref_time or get_ref_time > self.get_ref_time:
            try:
                self.set_ref_time(get_ref_time)
                fields = ['refname', 'objectname', 'committerdate:iso8601', 'authorname', 'authoremail', 'subject', 'committername', 'committeremail']
                fmt = "%00".join(["%(" + field + ")" for field in fields])
                git_refs = self._git(['for-each-ref', '--format', fmt, '--sort=-committerdate', 'refs/*/heads/*', 'refs/*/pull/*'])
                git_refs = git_refs.strip()
                if not git_refs:
                    return []
                refs = [tuple(field for field in line.split('\x00')) for line in git_refs.split('\n')]
                refs = [r for r in refs if dateutil.parser.parse(r[2][:19]) + datetime.timedelta(days=max_age) > datetime.datetime.now()]
                return refs
            except Exception:
                _logger.exception('Fail to get refs for repo %s', self.name)
                self.env['runbot.runbot'].warning('Fail to get refs for repo %s', self.name)
        return []

    def _find_or_create_branches(self, refs):
        """Parse refs and create branches that does not exists yet
        :param refs: list of tuples returned by _get_refs()
        :return: dict {branch.name: branch.id}
        The returned structure contains all the branches from refs newly created
        or older ones.
        """

        # FIXME WIP
        _logger.info('Cheking branches')
        names = [r[0].split('/')[-1] for r in refs]
        branches = self.env['runbot.branch'].search([('name', 'in', names), ('remote_id', 'in', self.remote_ids.ids)])
        ref_branches = {
            'refs/%s/%s/%s' % (
                branch.remote_id.remote_name,
                'pull' if branch.is_pr else 'heads',
                branch.name
                ): branch
            for branch in branches
        }
        for ref_name, sha, date, author, author_email, subject, committer, committer_email in refs:
            if not ref_branches.get(ref_name):
                # format example:
                # refs/ruodoo-dev/heads/12.0-must-fail
                # refs/ruodoo/pull/1
                _, remote_name, branch_type, name = ref_name.split('/')
                remote_id = self.remote_ids.filtered(lambda r: r.remote_name == remote_name).id
                if not remote_id:
                    _logger.warning('Remote %s not found', remote_name)
                    continue
                new_branch = self.env['runbot.branch'].create({'remote_id': remote_id, 'name': name, 'is_pr': branch_type == 'pull'})
                _logger.info('new branch %s (%s) found in %s', name, new_branch.id, self.name)
                ref_branches[ref_name] = new_branch
        return ref_branches

    def _find_new_commits(self, refs, ref_branches):
        """Find new commits in bare repo
        :param refs: list of tuples returned by _get_refs()
        :param ref_branches: dict structure {branch.name: branch.id}
                             described in _find_or_create_branches
        """
        self.ensure_one()
        _logger.info('Cheking commits %s', len(refs))

        for ref_name, sha, date, author, author_email, subject, committer, committer_email in refs:
            branch = ref_branches[ref_name]
            if branch.head_name != sha: # new push on branch
                _logger.info('repo %s branch %s new commit found: %s', self.name, branch.name, sha)

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
                # TODO add reflog -> history on commit found on branch

                if not self.trigger_ids:
                    continue

                bundle = branch.bundle_id
                if bundle.no_build:
                    continue

                if bundle.last_batch.state != 'preparing':
                    preparing = self.env['runbot.batch'].create({
                        'last_update': fields.Datetime.now(),
                        'bundle_id': bundle.id,
                        'state': 'preparing',
                    })
                    bundle.last_batch = preparing

                bundle.last_batch._new_commit(branch)

    def _update_batches(self, force=False):
        """ Find new commits in physical repos"""
        updated = False
        for repo in self:
            if repo.remote_ids and self._update(poll_delay=30 if force else 60*5):
                max_age = int(self.env['ir.config_parameter'].get_param('runbot.runbot_max_age', default=30))
                ref = repo._get_refs(max_age)
                ref_branches = repo._find_or_create_branches(ref)
                repo._find_new_commits(ref, ref_branches)
                _logger.info('</ new commit>')
                updated = True
        return updated

    def _update_git_config(self):
        """ Update repo git config file """
        for repo in self:
            if os.path.isdir(os.path.join(repo.path, 'refs')):
                git_config_path = os.path.join(repo.path, 'config')
                template_params = {'repo': repo}
                git_config = self.env['ir.ui.view'].render_template("runbot.git_config", template_params)
                with open(git_config_path, 'wb') as config_file:
                    config_file.write(git_config)
                _logger.info('Config updated for repo %s' % repo.name)
            else:
                _logger.info('Repo not cloned, skiping config update for %s' % repo.name)

    def _git_init(self):
        """ Clone the remote repo if needed """
        self.ensure_one()
        repo = self
        if not os.path.isdir(os.path.join(repo.path, 'refs')):
            _logger.info("Initiating repository '%s' in '%s'" % (repo.name, repo.path))
            git_init = subprocess.run(['git', 'init', '--bare', repo.path], stderr=subprocess.PIPE)
            if git_init.returncode:
                _logger.warning('Git init failed with code %s and message: "%s"', git_init.returncode, git_init.stderr)
                return
            self._update_git_config()
            self._update_git(True)

    def _update_git(self, force=False, poll_delay=5*60):
        """ Update the git repo on FS """
        self.ensure_one()
        repo = self
        if not repo.remote_ids:
            return False
        if not os.path.isdir(os.path.join(repo.path)):
            os.makedirs(repo.path)
        self._git_init()
        # TODO bare check repo in remotes

        # check for mode == hook
        fname_fetch_head = os.path.join(repo.path, 'FETCH_HEAD')
        if not force and os.path.isfile(fname_fetch_head):
            fetch_time = os.path.getmtime(fname_fetch_head)
            if repo.mode == 'hook':
                if not repo.hook_time or repo.hook_time < fetch_time:
                    return False
            if repo.mode == 'poll':
                if (time.time() < fetch_time + poll_delay):
                    return False

        _logger.info('Updating repo %s', repo.name)
        return self._update_fetch_cmd()

    def _update_fetch_cmd(self):
        # Extracted from update_git to be easily overriden in external module
        self.ensure_one()
        try_count = 0
        success = False
        delay = 0
        while not success and try_count < 5:
            time.sleep(delay)
            try:
                self._git(['fetch', '-p', '--all',])
                success = True
            except subprocess.CalledProcessError as e:
                try_count += 1
                delay = delay * 1.5 if delay else 0.5
                if try_count > 4:
                    message = 'Failed to fetch repo %s: %s' % (self.name, e.output.decode())
                    _logger.exception(message)
                    host = self.env['runbot.host']._get_current()
                    host.disable()
        return success

    def _update(self, force=False, poll_delay=5*60):
        """ Update the physical git reposotories on FS"""
        for repo in self:
            try:
                return repo._update_git(force, poll_delay)
            except Exception:
                _logger.exception('Fail to update repo %s', repo.name)


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
