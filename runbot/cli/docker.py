#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import os
import signal
import sys

import odoo

from odoo.cli import Command
from odoo.tools import config

from ..container import docker_list_images

def raise_keyboard_interrupt(*a):
    raise KeyboardInterrupt()


class Docker(Command):
    """Manage runbot docker images from cli"""

    def init(self, args):
        config.parse_config(args)
        odoo.cli.server.report_configuration()
        odoo.service.server.start(preload=[], stop=True)
        signal.signal(signal.SIGINT, raise_keyboard_interrupt)

    def _action_list_dockerfiles(self, env):
        images_on_host = {i['Tag']: i for i in docker_list_images() if i['Repository'] == 'odoo'}
        for dockerfile in env['runbot.dockerfile'].search([]):
            infos = images_on_host.get(dockerfile.image_tag, {})
            print('%s -- %s -- %s' % (dockerfile.name, infos.get('CreatedAt', ''), infos.get('ID', '')))

    def _run_action(self, action):
        with odoo.api.Environment.manage():
            registry = odoo.registry(config['db_name'])
            with registry.cursor() as cr:
                uid = odoo.SUPERUSER_ID
                ctx = odoo.api.Environment(cr, uid, {})['res.users'].context_get()
                env = odoo.api.Environment(cr, uid, ctx)
                action(env)
                cr.rollback()

    def run(self, cmdargs):
        parser = argparse.ArgumentParser(
            prog="%s docker" % sys.argv[0].split(os.path.sep)[-1],
            description=self.__doc__
        )

        parser.add_argument("--list", action="store_true", help="List docker images")
        parser.add_argument("-d", "--database", dest="db_name", help="specify the database name")

        if not cmdargs:
            sys.exit(parser.print_help())

        args = parser.parse_args(args=cmdargs)
        self.init(['-d', args.db_name])

        if args.list:
            self._run_action(self._action_list_dockerfiles)
