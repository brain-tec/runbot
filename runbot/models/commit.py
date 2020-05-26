
from ..common import os
import glob
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class Commit(models.Model):
    _name = "runbot.commit"
    _description = "Commit"

    #  TODO access rights

    _sql_constraints = [
        (
            "commit_unique",
            "unique (name, repo_id)",
            "Commit must be unique to ensure correct duplicate matching",
        )
    ]
    name = fields.Char('SHA')
    repo_id = fields.Many2one('runbot.repo', string='Repo group')
    date = fields.Datetime('Commit date')
    author = fields.Char('Author')
    author_email = fields.Char('Author Email')
    committer = fields.Char('Committer')
    committer_email = fields.Char('Committer Email')
    subject = fields.Text('Subject')
    dname = fields.Char('Display name', compute='_compute_dname')

    def _get_available_modules(self):
        for manifest_file_name in self.repo_id.manifest_files.split(','):  # '__manifest__.py' '__openerp__.py'
            for addons_path in (self.repo_id.addons_paths or '').split(','):  # '' 'addons' 'odoo/addons'
                sep = os.path.join(addons_path, '*')
                for manifest_path in glob.glob(self._source_path(sep, manifest_file_name)):
                    module = os.path.basename(os.path.dirname(manifest_path))
                    yield (addons_path, module, manifest_file_name)

    def export(self):
        return self.repo_id._git_export(self.name)

    def read_source(self, file, mode='r'):
        file_path = self._source_path(file)
        try:
            with open(file_path, mode) as f:
                return f.read()
        except:
            return False

    def _source_path(self, *path):
        return self.repo_id._source_path(self.name, *path)

    @api.depends('name', 'repo_id.name')
    def _compute_dname(self):
        for commit in self:
            commit.dname = '%s:%s' % (commit.repo_id.name, commit.name[:8])

    def _github_status(self, context, state, target_url, description=None):
        self.ensure_one()
        Status = self.env['runbot.commit.status']
        last_status = Status.search([('commit_id', '=', self.id), ('context', '=', context)], order='id desc', limit=1)
        if last_status and last_status.state == state:
            _logger.info('Skipping already sent status %s:%s for %s', context, state, self.name)
            return
        last_status = Status.create({
            'commit_id': self.id,
            'context': context,
            'state': state,
            'target_url': target_url,
            'description': description or context,
        })
        last_status.send()


class CommitStatus(models.Model):
    _name = 'runbot.commit.status'
    _description = 'Commit status'

    commit_id = fields.Many2one('runbot.commit', string='Commit', required=True, index=True)
    context = fields.Char('Context', required=True)
    state = fields.Char('State', required=True)
    target_url = fields.Char('Url')
    description = fields.Char('Description')

    def send(self):
        user_id = self.env.user.id
        _dbname = self.env.cr.dbname
        _context = self.env.context

        commit = self.commit_id
        remote_ids = commit.repo_id.remote_ids.filtered(lambda remote: remote.token).ids
        commit_name = commit.name

        status = {
            'context': self.context,
            'state': self.state,
            'target_url': self.target_url,
            'description': self.description,
        }
        if False and remote_ids:  # TODO remove this security False
            def send_github_status():
                try:
                    db_registry = registry(_dbname)
                    with api.Environment.manage(), db_registry.cursor() as cr:
                        env = api.Environment(cr, user_id, _context)
                        for remote in env['runbot.remote'].browse(remote_ids):
                            _logger.debug(
                                "github updating %s status %s to %s in repo %s",
                                status['context'], commit_name, status['state'], remote.name)
                            remote._github('/repos/:owner/:repo/statuses/%s' % commit_name, status, ignore_errors=True)
                except:
                    _logger.exception('Something went wrong sending notification for %s', commit_name)

            self._cr.after('commit', send_github_status)

class RunbotCommitLink(models.Model):
    _name = "runbot.commit.link"
    _description = "Build commit"

    commit_id = fields.Many2one('runbot.commit', 'Commit', required=True)
    # Link info
    match_type = fields.Selection([('new', 'New head of branch'), ('head', 'Head of branch'), ('base', 'Found on base branch')])  # HEAD, DEFAULT
    branch_id = fields.Many2one('runbot.branch', string='Found in branch')  # Shouldn't be use for anything else than display

    base_commit_id = fields.Many2one('runbot.commit', 'Base head commit')
    merge_base_commit_id = fields.Many2one('runbot.commit', 'Merge Base commit')
    base_behind = fields.Integer('# commits behind base')
    base_ahead = fields.Integer('# commits ahead base')
    file_changed = fields.Integer('# file changed')
    diff_add = fields.Integer('# line added')
    diff_remove = fields.Integer('# line removed')
