import logging

try:
    from odoo.upgrade import util
except ImportError:
    util = None


_logger = logging.getLogger(__name__)


def migrate(cr, _version):
    if not util:
        _logger.error('Missing util, cannot execute upgrade')
        return
    # Replace the notation $$fa-foo$$ with [@icon-foo](path)
    # and then cleans up messages in the notation $$message$$ if there are any.
    util.explode_execute(cr, r"""
        UPDATE ir_logging
           SET message =
                REPLACE(
                    REGEXP_REPLACE(
                        message,
                        '(.*)\$\$fa-([a-zA-Z]*)\$\$(.*)',
                        CONCAT('\1[@icon-\2](', "path", ')\3')
                    ),
                    '$$',
                    ''
                ),
               type = 'markdown'
         WHERE type = 'link'
    """, 'ir_logging', bucket_size=100000)
