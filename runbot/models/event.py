# -*- coding: utf-8 -*-

import logging
from collections import defaultdict

from odoo import models, fields, api
from odoo.addons.runbot.models.build_error import clean, digest

_logger = logging.getLogger(__name__)

TYPES = [(t, t.capitalize()) for t in 'client server runbot subbuild'.split()]


class runbot_event(models.Model):

    _inherit = "ir.logging"
    _order = 'id'

    build_id = fields.Many2one('runbot.build', 'Build', index=True, ondelete='cascade')
    type = fields.Selection(TYPES, string='Type', required=True, index=True)

    @api.model_cr
    def init(self):
        parent_class = super(runbot_event, self)
        if hasattr(parent_class, 'init'):
            parent_class.init()

        self._cr.execute("""
CREATE OR REPLACE FUNCTION runbot_set_logging_build() RETURNS TRIGGER AS $runbot_set_logging_build$
BEGIN
  IF (NEW.build_id IS NULL AND NEW.dbname IS NOT NULL AND NEW.dbname != current_database()) THEN
    NEW.build_id := split_part(NEW.dbname, '-', 1)::integer;
  END IF;
  IF (NEW.build_id IS NOT NULL AND UPPER(NEW.level) NOT IN ('INFO', 'SEPARATOR')) THEN
    BEGIN
        UPDATE runbot_build b
            SET triggered_result = CASE WHEN UPPER(NEW.level) = 'WARNING' THEN 'warn'
                                        ELSE 'ko'
                                   END
        WHERE b.id = NEW.build_id;
    END;
  END IF;
RETURN NEW;
END;
$runbot_set_logging_build$ language plpgsql;

DROP TRIGGER IF EXISTS runbot_new_logging ON ir_logging;
CREATE TRIGGER runbot_new_logging BEFORE INSERT ON ir_logging
FOR EACH ROW EXECUTE PROCEDURE runbot_set_logging_build();

        """)

    def _parse_logs(self):
        """ Parse logs to classify errors """
        BuildError = self.env['runbot.build.error']
        multibuild_config_id = self.env.ref('runbot.runbot_build_config_light_test').id

        candidates = self.env['runbot.build'].search([
            ('branch_id.branch_name', 'in', ['master']),
            ('branch_id.sticky', '=', True),
            ('create_date', '>', '2019-06-10'),
            ('config_id', '=', multibuild_config_id),
            ('repo_id', 'in', [1, 7])])  # should be configurable

        query = """
        SELECT build_id, name, message
            FROM ir_logging
            WHERE
                type='server'
                AND level='ERROR'
                AND length(message) > 50
                AND build_id NOT IN (SELECT DISTINCT build_id FROM runbot_build_error_ids_runbot_build_rel)
                AND build_id IN %s
        """
        self.env.cr.execute(query, (tuple(candidates.ids), ))
        ir_logs = self.env.cr.fetchall()
        _logger.info('Logs found: %s', len(ir_logs))

        hash_dict = defaultdict(list)
        for log in ir_logs:
            hash = digest(clean(log[2]))
            hash_dict[hash].append(log)

        # update the errors
        for build_error in BuildError.search([('hash', 'in', list(hash_dict.keys()))]):
            build_error.write({'build_ids': [(0, False, hash_dict[build_error.hash]['build_id'])]})
            del hash_dict[build_error.hash]

        # create an error for the remaining entries
        for hash, rows in hash_dict.items():
            BuildError.create({
                'content': rows[0][2],
                'module_name': rows[0][1],
                'build_ids': [(6, False, [int(r[0]) for r in rows])]
            })
