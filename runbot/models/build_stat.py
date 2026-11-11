import datetime
import logging
import time

from odoo import models, fields, api, tools
from ..fields import JsonDictField

_logger = logging.getLogger(__name__)


class BuildStat(models.Model):
    _name = "runbot.build.stat"
    _description = "Statistics"
    _log_access = False

    _build_config_key_unique = models.Constraint(
        'unique (build_id, config_step_id, category, dynamic_step_name)',
        "Build stats must be unique for the same build step",
    )

    build_id = fields.Many2one("runbot.build", "Build", ondelete="cascade")  # indexed by _build_config_key_unique
    config_step_id = fields.Many2one(
        "runbot.build.config.step", "Step", ondelete="cascade",
    )
    dynamic_step_name = fields.Char("Dynamic Step Name")
    category = fields.Char("Category", index=True)
    values = JsonDictField("Value")

    def _gc_stats(self):
        """ Remove old stats:
            - after `runbot.stats_gc_days_non_sticky` days (default 180): stats of builds not used by a sticky bundle
            - after `runbot.stats_gc_days_all` days (default 0, disabled): all stats

            Builds are processed by id ranges, the position is saved in `runbot.stats_gc_last_build_id_<scope>`
            so that the backlog is processed over multiple runs and each run then only processes the newly old builds.
            When the time limit (`runbot.stats_gc_time_limit`, default 60s) is reached, the remaining work is
            reported to the cron (`_commit_progress`) so that it is executed again as soon as possible.
        """
        icp = self.env['ir.config_parameter'].sudo()
        end_time = time.time() + int(icp.get_param('runbot.stats_gc_time_limit', default=60))
        remaining = 0
        for scope, default_days in (('non_sticky', 180), ('all', 0)):
            remaining += self._gc_stats_builds(scope, default_days, end_time)
        self.env['ir.cron']._commit_progress(remaining=remaining)

    def _gc_stats_builds(self, scope, default_days, end_time):
        """ Process the build ranges of one scope until `end_time`, returns the number of remaining ranges """
        icp = self.env['ir.config_parameter'].sudo()
        gc_days = int(icp.get_param(f'runbot.stats_gc_days_{scope}', default=default_days))
        if gc_days <= 0:
            return 0
        batch_size = int(icp.get_param('runbot.stats_gc_batch_size', default=100000))
        position_key = f'runbot.stats_gc_last_build_id_{scope}'
        last_id = int(icp.get_param(position_key, default=0))
        cutoff = self.env.cr.now() - datetime.timedelta(days=gc_days)

        # first build that is too recent to be cleaned (create_date index)
        self.env.cr.execute("SELECT id FROM runbot_build WHERE create_date >= %s ORDER BY create_date LIMIT 1", [cutoff])
        row = self.env.cr.fetchone()
        if not row:
            return 0
        max_id = row[0]

        if scope == 'non_sticky':
            # A build is kept if its top parent is in a slot of a sticky batch, or if it is linked
            # (runbot.build.link, single level) to a build whose top parent is in a slot of a sticky batch.
            query = """
                WITH b AS (
                    SELECT id, split_part(parent_path, '/', 1)::int AS top_id
                    FROM runbot_build
                    WHERE id >= %(lo)s AND id < %(hi)s AND create_date < %(cutoff)s
                ), tops AS (
                    SELECT DISTINCT top_id FROM b
                ), keep AS (
                    SELECT t.top_id
                    FROM tops t
                    JOIN runbot_batch_slot sl ON sl.build_id = t.top_id
                    JOIN runbot_batch ba ON ba.id = sl.batch_id
                    JOIN runbot_bundle bu ON bu.id = ba.bundle_id AND bu.sticky
                    UNION
                    SELECT t.top_id
                    FROM tops t
                    JOIN runbot_build_link l ON l.child_id = t.top_id
                    JOIN runbot_build p ON p.id = l.parent_id
                    JOIN runbot_batch_slot sl ON sl.build_id = split_part(p.parent_path, '/', 1)::int
                    JOIN runbot_batch ba ON ba.id = sl.batch_id
                    JOIN runbot_bundle bu ON bu.id = ba.bundle_id AND bu.sticky
                ), attached AS (
                    -- only consider builds belonging to a batch (directly or through a link),
                    -- builds without batch (manual builds, ...) are ignored
                    SELECT t.top_id FROM tops t
                    WHERE EXISTS (SELECT 1 FROM runbot_batch_slot sl WHERE sl.build_id = t.top_id)
                    OR EXISTS (SELECT 1 FROM runbot_build_link l WHERE l.child_id = t.top_id)
                )
                DELETE FROM runbot_build_stat s
                USING b
                WHERE s.build_id = b.id
                AND b.top_id IN (SELECT top_id FROM attached)
                AND b.top_id NOT IN (SELECT top_id FROM keep)
            """
        else:
            query = """
                DELETE FROM runbot_build_stat s
                USING runbot_build b
                WHERE s.build_id = b.id
                AND b.id >= %(lo)s AND b.id < %(hi)s AND b.create_date < %(cutoff)s
            """

        total = 0
        while last_id < max_id and time.time() < end_time:
            hi = min(last_id + batch_size, max_id)
            self.env.cr.execute(query, {'lo': last_id, 'hi': hi, 'cutoff': cutoff})
            total += self.env.cr.rowcount
            last_id = hi
            icp.set_param(position_key, last_id)
            self.env['ir.cron']._commit_progress(1)
        _logger.info('Removed %s stats (%s) older than %s days (up to build %s)', total, scope, gc_days, last_id)
        return -(-(max_id - last_id) // batch_size)  # remaining ranges, rounded up
