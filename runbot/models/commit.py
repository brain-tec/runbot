import os
import glob
from odoo import models, fields, api

class Commit(models.Model):
    _name = "runbot.commit"
    _description = "Commit"

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
        for manifest_file_name in self.repo.manifest_files.split(','):  # '__manifest__.py' '__openerp__.py'
            for addons_path in (self.repo.addons_paths or '').split(','):  # '' 'addons' 'odoo/addons'
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


class RunbotBuildCommit(models.Model):
    _name = "runbot.build.commit"
    _description = "Build commit"

    params_id = fields.Many2one('runbot.build.params', 'Build', required=True, ondelete='cascade', index=True)
    commit_id = fields.Many2one('runbot.commit', 'Dependency commit', required=True)
    match_type = fields.Char('Match Type')
    #git_url = fields.Char('Url to commit', compute='_compute_commit_url')


