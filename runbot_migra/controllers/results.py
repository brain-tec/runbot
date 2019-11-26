# -*- coding: utf-8 -*-
from odoo.http import Controller, request, route


class RunbotMigration(Controller):

    @route(['/migrabot/project/<int:project_id>', '/migrabot/project'], type='http', auth="user", website=True)
    def project(self, project_id=None, **post):

        if not project_id:
            context = {
                'projects': request.env['runbot_migra.project'].search([])
            }
            return request.render("runbot_migra.project_list", context)

        project = request.env['runbot_migra.project'].browse([project_id])[0]
        if not project.exists():
            return request.not_found()

        context = {
            'project': project,
        }
        return request.render("runbot_migra.project", context)

    @route(['/migrabot/build/<int:build_id>'], type='http', auth="user", website=True)
    def build(self, build_id=None, **post):

        if not build_id:
            return request.not_found()

        build = request.env['runbot_migra.build'].browse([build_id])[0]
        if not build.exists():
            return request.not_found()

        try:
            migrate_log = open(build.log_update, 'r').read()
        except FileNotFoundError:
            migrate_log = ''

        context = {
            'build': build,
            'migrate_log': migrate_log,
        }
        return request.render("runbot_migra.build", context)
