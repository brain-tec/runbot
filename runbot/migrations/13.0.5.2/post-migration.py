# -*- coding: utf-8 -*-
from odoo.api import Environment
from odoo import SUPERUSER_ID

def migrate(cr, _version):
    env = Environment(cr, SUPERUSER_ID, {})
    # create a repo_hook for each repo to be able to use hooked related
    for repo in env['runbot.repo'].search([]):
        repo_hook = env['runbot.repo.hook'].create({})
        repo.repo_hook_id = repo_hook.id
