from odoo import models, fields, api

class Commit(models.Model):
    _name = "runbot.commit"
    _description = "Commit"

    _sql_constraints = [
        (
            "commit_unique",
            "unique (name, repo_group_id)",
            "Commit must be unique to ensure correct duplicate matching",
        )
    ]
    name = fields.Char('SHA')
    repo_group_id = fields.Many2one('runbot.repo_group', string='Repo group')
    date = fields.Datetime('Commit date')
    author = fields.Char('Author')
    author_email = fields.Char('Author Email')
    committer = fields.Char('Committer')
    committer_email = fields.Char('Committer Email')
    subject = fields.Text('Subject')
    dname = fields.Char('Display name', compute='_compute_dname')
    git_url = fields.Char('Url to commit', compute='_compute_commit_url')

    def _source_path(self, *path):
        return self.repo._source_path(self.name, *path)

    def export(self):
        return self.repo._git_export(self.name)

    def read_source(self, file, mode='r'):
        file_path = self._source_path(file)
        try:
            with open(file_path, mode) as f:
                return f.read()
        except:
            return False

    @api.depends('name', 'repo_group_id.name')
    def _compute_dname(self):
        for commit in self:
            commit.dname = '%s:%s' % (commit.repo_group_id.name, commit.name[:8])

    @api.depends('name', 'repo_id.base')
    def _compute_commit_url(self):
        """compute the branch url based on branch_name"""
        for commit in self:
            commit.git_url = 'https://%s/commit/%s' % (commit.repo_id.base, commit.name)


class RunbotBuildCommit(models.Model):
    _name = "runbot.build.commit"
    _description = "Build commit"

    params_id = fields.Many2one('runbot.build.params', 'Build', required=True, ondelete='cascade', index=True)
    commit_id = fields.Many2one('runbot.commit', 'Dependency commit', required=True)
    repo_id = fields.Many2one('runbot.repo', string='Repo') # discovered in repo
    closest_branch_id = fields.Many2one('runbot.branch', 'Branch', ondelete='cascade') # TODO remove?
    match_type = fields.Char('Match Type')

    def _get_repo(self):
        raise NotImplementedError()
        return self.closest_branch_id.repo_id or self.dependecy_repo_id

