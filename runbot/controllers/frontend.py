# -*- coding: utf-8 -*-
import operator
import werkzeug
import logging

from collections import OrderedDict

import werkzeug.utils
import werkzeug.urls

from odoo.addons.http_routing.models.ir_http import slug
from odoo.addons.website.controllers.main import QueryURL

from odoo.http import Controller, request, route
from ..common import uniq_list, flatten, fqdn
from odoo.osv import expression

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class Runbot(Controller):

    def _pending(self):
        ICP = request.env['ir.config_parameter'].sudo().get_param
        warn = int(ICP('runbot.pending.warning', 5))
        crit = int(ICP('runbot.pending.critical', 12))
        pending_count = request.env['runbot.build'].search_count([('local_state', '=', 'pending'), ('build_type', '!=', 'scheduled')])
        scheduled_count = request.env['runbot.build'].search_count([('local_state', '=', 'pending'), ('build_type', '=', 'scheduled')])
        level = ['info', 'warning', 'danger'][int(pending_count > warn) + int(pending_count > crit)]
        return pending_count, level, scheduled_count

    @route(['/', '/runbot', '/runbot/<model("runbot.project"):project>'], website=True, auth='public', type='http')
    def bundles(self, project=None, more=False, search='', refresh='', **kwargs):
        search = search if len(search) < 60 else search[:60]
        env = request.env
        projects = env['runbot.project'].search([])
        if not project and projects:
            project = projects[0]

        pending_count, level, scheduled_count = self._pending()
        context = {
            'projects': projects,
            'project': project,
            'search': search,
            'refresh': refresh,
            'message': request.env['ir.config_parameter'].sudo().get_param('runbot.runbot_message'),
            'pending_total': pending_count,
            'pending_level': level,
            'scheduled_count': scheduled_count,
            'hosts_data': request.env['runbot.host'].search([]),
            'more': more is not False,
        }

        if project:
            # basic search to start, only bundle name. TODO add batch.commits and bundle pr numbers (all branches names)

            domain = [('last_batch', '!=', False), ('project_id', '=', project.id), ('no_build', '=', False)]
            if search:
                search_domain = expression.OR([[('name', 'like', search_elem)] for search_elem in search.split("|")])
                domain = expression.AND([domain, search_domain])

            e = expression.expression(domain, request.env['runbot.bundle'])
            where_clause, where_params = e.to_sql()
            env.cr.execute("""
                SELECT id FROM runbot_bundle
                WHERE {where_clause}
                ORDER BY
                    (case when sticky then 1 when sticky is null then 2 else 2 end),
                    case when sticky then version_number end collate "C" desc,
                    last_batch desc
                LIMIT 100""".format(where_clause=where_clause), where_params)
            bundles = env['runbot.bundle'].browse([r[0] for r in env.cr.fetchall()])

            context.update({
                'bundles': bundles,
                'qu': QueryURL('/runbot/' + slug(project), search=search, refresh=refresh),
            })

        context.update({'message': request.env['ir.config_parameter'].sudo().get_param('runbot.runbot_message')})
        return request.render('runbot.bundles', context)

    @route([
        '/runbot/bundle/<model("runbot.bundle"):bundle>',
        '/runbot/bundle/<model("runbot.bundle"):bundle>/page/<int:page>'
        ], website=True, auth='public', type='http')
    def bundle(self, bundle=None, page=1, limit=50, more=False, **kwargs):
        env = request.env
        domain = [('bundle_id', '=', bundle.id), ('hidden', '=', False)]
        batch_count = request.env['runbot.batch'].search_count(domain)
        pager = request.website.pager(
            url='/runbot/bundle/%s' % bundle.id,
            total=batch_count,
            page=page,
            step=50,
        )
        batchs = request.env['runbot.batch'].search(domain, limit=limit, offset=pager.get('offset', 0), order='id desc')

        context = {
            'bundle': bundle,
            'batchs': batchs,
            'pager': pager,
            'more': more is not False,
            'projects': request.env['runbot.project'].search([]),
            'project': bundle.project_id
            }

        return request.render('runbot.bundle', context)

    @route([
        '/runbot/bundle/<model("runbot.bundle"):bundle>/force',
    ], type='http', auth="user", methods=['GET', 'POST'], csrf=False)
    def force_bundle(self, bundle, **post):
        _logger.info('user %s forcing bundle %s', request.env.user.name, bundle.name) # user must be able to read bundle
        batch = bundle.sudo()._force()
        return werkzeug.utils.redirect('/runbot/batch/%s' % batch.id)

    @route(['/runbot/batch/<int:batch_id>'], website=True, auth='public', type='http')
    def batch(self, batch_id=None, more=False, **kwargs):
        batch = request.env['runbot.batch'].browse(batch_id)
        context = {
            'batch': batch,
            'more': more is not False,
            'projects': request.env['runbot.project'].search([]),
            'project': batch.bundle_id.project_id,
        }
        return request.render('runbot.batch', context)

    @route(['/runbot/commit/<model("runbot.commit"):commit>'], website=True, auth='public', type='http')
    def commit(self, commit=None, more=False, **kwargs):
        context = {
            'commit': commit,
            'more': more is not False,
            'projects': request.env['runbot.project'].search([]),
            'project': commit.repo_id.project_id,
            'reflogs': request.env['runbot.ref.log'].search([('commit_id', '=', commit.id)])
        }
        return request.render('runbot.commit', context)

    @route([
        '/runbot/build/<int:build_id>/<operation>',
    ], type='http', auth="public", methods=['POST'], csrf=False)
    def build_operations(self, build_id, operation, exact=0, search=None, **post):
        build = request.env['runbot.build'].sudo().browse(build_id)
        if operation == 'rebuild':
            build = build._rebuild()
        elif operation == 'kill':
            build._ask_kill()
        elif operation == 'wakeup':
            build._wake_up()

        qs = ''
        if search:
            qs = '?' + werkzeug.urls.url_encode({'search': search})
        return werkzeug.utils.redirect(build.build_url + qs)

    @route(['/runbot/build/<int:build_id>'], type='http', auth="public", website=True)
    def build(self, build_id, more=False, search=None, **post):
        """Events/Logs"""

        Build = request.env['runbot.build']

        build = Build.browse([build_id])[0]
        if not build.exists():
            return request.not_found()

        context = {
            'build': build,
            'fqdn': fqdn(),
            'more': more is not False,
            'projects': request.env['runbot.project'].search([]),
            #'project': build_id.param_id.project_id, TODO how to find project? store cat? trigger?
        }
        return request.render("runbot.build", context)

    @route('/runbot/glances', type='http', auth='public', website=True)
    def glances(self, refresh=None):
        bundles = request.env['runbot.bundle'].search([('sticky', '=', True)]) # NOTE we dont filter on project
        pending = self._pending()
        qctx = {
            'refresh': refresh,
            'pending_total': pending[0],
            'pending_level': pending[1],
            'bundles': bundles,
        }
        return request.render("runbot.glances", qctx)

    @route(['/runbot/monitoring',
            '/runbot/monitoring/<int:config_id>',
            '/runbot/monitoring/<int:config_id>/<int:view_id>'], type='http', auth='user', website=True)
    def monitoring(self, config_id=None, view_id=None, refresh=None, **kwargs):
        pending = self._pending()
        hosts_data = request.env['runbot.host'].search([])

        last_monitored = None

        monitored_config_id = config_id or int(request.env['ir.config_parameter'].sudo().get_param('runbot.monitored_config_id', 1))
        request.env.cr.execute("""SELECT DISTINCT ON (branch_id) branch_id, id FROM runbot_build
                                WHERE config_id = %s
                                AND global_state in ('running', 'done')
                                AND branch_id in (SELECT id FROM runbot_branch where sticky='t')
                                AND local_state != 'duplicate'
                                ORDER BY branch_id ASC, id DESC""", [int(monitored_config_id)])
        last_monitored = request.env['runbot.build'].browse([r[1] for r in request.env.cr.fetchall()])

        config = request.env['runbot.build.config'].browse(monitored_config_id)
        qctx = {
            'config': config,
            'refresh': refresh,
            'pending_total': pending[0],
            'pending_level': pending[1],
            'scheduled_count': pending[2],
            'glances_data': glances_ctx,
            'hosts_data': hosts_data,
            'last_monitored': last_monitored,  # nightly
            'auto_tags': request.env['runbot.build.error'].disabling_tags(),
            'build_errors': request.env['runbot.build.error'].search([('random', '=', True)]),
            'kwargs': kwargs
        }
        return request.render(view_id if view_id else config.monitoring_view_id.id or "runbot.monitoring", qctx)

    @route(['/runbot/config/<int:config_id>',
            '/runbot/config/<config_name>'], type='http', auth="public", website=True)
    def config(self, config_id=None, config_name=None, refresh=None, **kwargs):

        if config_id:
            monitored_config_id = config_id
        else:
            config = request.env['runbot.build.config'].search([('name', '=', config_name)], limit=1)
            if config:
                monitored_config_id = config.id
            else:
                raise UserError('Config name not found')

        readable_repos = request.env['runbot.repo'].search([])
        request.env.cr.execute("""SELECT DISTINCT ON (branch_id) branch_id, id FROM runbot_build
                                WHERE config_id = %s
                                AND global_state in ('running', 'done')
                                AND branch_id in (SELECT id FROM runbot_branch where sticky='t' and repo_id in %s)
                                AND local_state != 'duplicate'
                                ORDER BY branch_id ASC, id DESC""", [int(monitored_config_id), tuple(readable_repos.ids)])
        last_monitored = request.env['runbot.build'].browse([r[1] for r in request.env.cr.fetchall()])

        config = request.env['runbot.build.config'].browse(monitored_config_id)
        qctx = {
            'config': config,
            'refresh': refresh,
            'last_monitored': last_monitored,  # nightly
            'kwargs': kwargs
        }
        return request.render(config.monitoring_view_id.id or "runbot.config_monitoring", qctx)
