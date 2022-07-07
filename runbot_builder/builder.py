#!/usr/bin/python3
from odoo import sql_db
from odoo.addons.runbot.tools import list_local_dbs, local_pgadmin_cursor
from tools import RunbotClient, run
import logging

_logger = logging.getLogger(__name__)

class BuilderClient(RunbotClient):

    def on_start(self):
        if 'runbot_logs' not in list_local_dbs():
            _logger.info('Logging database not found. Creating it ...')
            with local_pgadmin_cursor() as local_cr:
                db_logs = 'runbot_logs'  # hard coded for tests
                local_cr.execute(f"""CREATE DATABASE "{db_logs}" TEMPLATE template0 LC_COLLATE 'C' ENCODING 'unicode'""")

            with sql_db.db_connect('runbot_logs').cursor() as cr:
                # create_date, type, dbname, name, level, message, path, line, func
                cr.execute("""CREATE TABLE ir_logging (
                    id bigserial NOT NULL,
                    create_uid integer,
                    create_date timestamp without time zone,
                    name character varying NOT NULL,
                    level character varying,
                    dbname character varying,
                    func character varying NOT NULL,
                    path character varying NOT NULL,
                    line character varying NOT NULL,
                    type character varying NOT NULL,
                    message text NOT NULL);
                """)

        for repo in self.env['runbot.repo'].search([('mode', '!=', 'disabled')]):
            repo._update(force=True)

    def loop_turn(self):
        if self.count == 1: # cleanup at second iteration
            self.env['runbot.runbot']._source_cleanup()
            self.env['runbot.build']._local_cleanup()
            self.env['runbot.runbot']._docker_cleanup()
            self.host.set_psql_conn_count()
            self.host._docker_build()
            self.env['runbot.repo']._update_git_config()
            self.git_gc()
        return self.env['runbot.runbot']._scheduler_loop_turn(self.host)


if __name__ == '__main__':
    run(BuilderClient)
