# -*- coding: utf-8 -*-

import logging
from odoo import models, fields, api


_logger = logging.getLogger(__name__)


class Repo(models.Model):

    _name = "runbot_migra.repo"
    _description = "Github repository"

    name = fields.Char('Repository', required=True)
