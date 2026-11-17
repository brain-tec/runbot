import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if column_exists(cr, 'runbot_codeowner', 'match_type'):
        _logger.info('Column runbot_codeowner.match_type already exists, skipping')
        return

    # existing codeowners are all file path rules
    cr.execute("ALTER TABLE runbot_codeowner ADD COLUMN match_type VARCHAR DEFAULT 'file'")
    cr.execute("ALTER TABLE runbot_codeowner ALTER COLUMN match_type DROP DEFAULT")
