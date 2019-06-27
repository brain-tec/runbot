# -*- coding: utf-8 -*-
import hashlib
import logging
import re

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

CLEANING_REGS = (
    re.compile(r', line \d+,'),
)


def clean(s):
    """
    Clean the string s with the cleaning regs
    Replacing the regex with a space
    """
    for r in CLEANING_REGS:
        s = r.sub(' ', s, re.MULTILINE)
    return s


def digest(s):
    """
    return a hash 256 digest of the string s
    """
    return hashlib.sha256(s.encode()).hexdigest()


class RunbotBuildError(models.Model):

    _name = "runbot.build.error"

    content = fields.Text('Error message', required=True)
    cleaned_content = fields.Text('Cleaned error message')
    module_name = fields.Char('Module name')  # name in ir_logging
    hash = fields.Char('Error fingerprint', index=True)
    responsible = fields.Char('Fixer')  # many2one to res.user ?
    fixing_commit = fields.Char('Commit that should fix the problem')
    build_ids = fields.Many2many('runbot.build', 'runbot_build_error_ids_runbot_build_rel',
                                 column1='build_error_id', column2='build_id',
                                 string='Affected builds')
    active = fields.Boolean('Error is not fixed', default=True)

    @api.multi
    def create(self, vals):
        content = vals.get('content')
        cleaned_content = clean(content)
        vals.update({'cleaned_content': cleaned_content})
        vals.update({'hash': digest(cleaned_content)})
        return super().create(vals)
