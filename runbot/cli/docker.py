#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import os
import signal
import sys
import tempfile

from pathlib import Path

import odoo

from odoo.cli import Command
from odoo.tools import config

from ..container import docker_list_images, docker_build

def raise_keyboard_interrupt(*a):
    raise KeyboardInterrupt()


class Docker(Command):
    """Manage runbot docker images from cli"""

    def init(self, args):
        config.parse_config(args)
        odoo.cli.server.report_configuration()
        odoo.service.server.start(preload=[], stop=True)
        signal.signal(signal.SIGINT, raise_keyboard_interrupt)

    def _action_list_dockerfiles(self, env, args):
        images_on_host = {i['Tag']: i for i in docker_list_images() if i['Repository'] == 'odoo'}
        for dockerfile in env['runbot.dockerfile'].search([]):
            infos = images_on_host.get(dockerfile.image_tag, {})
            print('%s -- %s -- %s -- %s' % (dockerfile.name, dockerfile.image_tag, infos.get('CreatedAt', ''), infos.get('ID', '')))

    def _action_show_dockerfile(self, env, args):
        dockerfile = env['runbot.dockerfile'].search([('image_tag', '=', args.show_tag)])
        if not dockerfile or len(dockerfile) > 1:
            return
        print(dockerfile.dockerfile)

    def _action_build_dockerfile(self, env, args):
        dockerfile = env['runbot.dockerfile'].search([('image_tag', '=', args.build_tag)])
        if not dockerfile or len(dockerfile) > 1:
            return

        tdir = tempfile.TemporaryDirectory(prefix='runbot_dockerbuild_')
        build_dir = Path(tdir.name)
        dockerfile_path = build_dir.joinpath('Dockerfile')
        print('Building docker image: %s' % dockerfile_path)
        dockerfile_path.write_text(dockerfile.dockerfile)
        docker_build(build_dir, args.build_tag)

    def _run_action(self, action, args):
        with odoo.api.Environment.manage():
            registry = odoo.registry(config['db_name'])
            with registry.cursor() as cr:
                uid = odoo.SUPERUSER_ID
                ctx = odoo.api.Environment(cr, uid, {})['res.users'].context_get()
                env = odoo.api.Environment(cr, uid, ctx)
                action(env, args)
                cr.rollback()

    def run(self, cmdargs):
        parser = argparse.ArgumentParser(
            prog="%s docker" % sys.argv[0].split(os.path.sep)[-1],
            description=self.__doc__
        )

        parser.add_argument("-d", "--database", dest="db_name", help="specify the database name")
        action_group = parser.add_mutually_exclusive_group(required=True)
        action_group.add_argument("--list", action="store_true", help="List docker images")
        action_group.add_argument("--show", dest="show_tag", help="Show the runbot Dockerfile ")
        action_group.add_argument("--build", dest="build_tag", help="Show the runbot Dockerfile ")

        if not cmdargs:
            sys.exit(parser.print_help())

        args = parser.parse_args(args=cmdargs)
        self.init(['-d', args.db_name])

        if args.list:
            self._run_action(self._action_list_dockerfiles, args)
        elif args.show_tag:
            self._run_action(self._action_show_dockerfile, args)
        elif args.build_tag:
            self._run_action(self._action_build_dockerfile, args)
