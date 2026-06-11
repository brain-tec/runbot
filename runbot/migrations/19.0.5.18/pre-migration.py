from odoo.upgrade import util


def migrate(cr, version):
    util.rename_field(cr, 'runbot.bundle', 'has_pr', 'has_active_pr')
