# -*- coding: utf-8 -*-
import operator
import werkzeug
import logging
import functools

from collections import OrderedDict

import werkzeug.utils
import werkzeug.urls

from odoo.addons.http_routing.models.ir_http import slug
from odoo.addons.website.controllers.main import QueryURL

from odoo.http import Controller, request, route as o_route
from odoo.osv import expression

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

def route(routes, **kw):
    def decorator(f):
        @o_route(routes, **kw)
        @functools.wraps(f)
        def response_wrap(*args, **kwargs):
            projects = request.env['runbot.project'].search([])
            kwargs['projects'] = projects
            response = f(*args, **kwargs)

            keep_search = request.httprequest.cookies.get('keep_search', False) == '1'
            response.qcontext['keep_search'] = keep_search
            cookie_search = request.httprequest.cookies.get('search', '')

            if keep_search and cookie_search and 'search' not in kwargs:
                search = cookie_search
            else:
                search = kwargs.get('search', '')
            if keep_search and cookie_search != search:
                response.set_cookie('search', search)
            response.qcontext['search'] = search
            response.qcontext['current_path'] = request.httprequest.full_path
            refresh = kwargs.get('refresh', False)
            project = response.qcontext.get('project') or projects[0]
            response.qcontext['refresh'] = refresh
            response.qcontext['qu'] = QueryURL('/runbot/%s' % (slug(project)), path_args=['search'], search=search, refresh=refresh)

            return response
        return response_wrap
    return decorator

class Runbot(Controller):

    def _pending(self):
        ICP = request.env['ir.config_parameter'].sudo().get_param
        warn = int(ICP('runbot.pending.warning', 5))
        crit = int(ICP('runbot.pending.critical', 12))
        pending_count = request.env['runbot.build'].search_count([('local_state', '=', 'pending'), ('build_type', '!=', 'scheduled')])
        scheduled_count = request.env['runbot.build'].search_count([('local_state', '=', 'pending'), ('build_type', '=', 'scheduled')])
        level = ['info', 'warning', 'danger'][int(pending_count > warn) + int(pending_count > crit)]
        return pending_count, level, scheduled_count

    @o_route([
        '/runbot/submit'
    ], type='http', auth="public", methods=['GET', 'POST'], csrf=False)
    def submit(self, more=False, redirect='/', keep_search=False, category=False, filter_mode=False, update_triggers=False, **kwargs):
        response = werkzeug.utils.redirect(redirect)
        response.set_cookie('more', '1' if more else '0')
        response.set_cookie('keep_search', '1' if keep_search else '0')
        response.set_cookie('filter_mode', filter_mode or 'all')
        response.set_cookie('category', category or '0')
        if update_triggers:
            enabled_triggers = []
            project_id = int(update_triggers)
            for key in kwargs.keys():
                if key.startswith('trigger_'):
                    enabled_triggers.append(key.replace('trigger_', ''))

            key = 'trigger_display_%s' % project_id
            if len(request.env['runbot.trigger'].search([('project_id', '=', project_id)])) == len(enabled_triggers):
                response.delete_cookie(key)
            else:
                response.set_cookie(key, '-'.join(enabled_triggers))
        return response

    @route(['/',
            '/runbot',
            '/runbot/<model("runbot.project"):project>',
            '/runbot/<model("runbot.project"):project>/search/<search>'], website=True, auth='public', type='http')
    def bundles(self, project=None, search='', projects=False, refresh=False, **kwargs):
        search = search if len(search) < 60 else search[:60]
        env = request.env
        categories = env['runbot.trigger.category'].search([])
        if not project and projects:
            project = projects[0]

        pending_count, level, scheduled_count = self._pending()
        context = {
            'categories': categories,
            'search': search,
            'message': request.env['ir.config_parameter'].sudo().get_param('runbot.runbot_message'),
            'pending_total': pending_count,
            'pending_level': level,
            'scheduled_count': scheduled_count,
            'hosts_data': request.env['runbot.host'].search([]),
        }
        if project:
            # basic search to start, only bundle name. TODO add batch.commits and bundle pr numbers (all branches names)

            domain = [('last_batch', '!=', False), ('project_id', '=', project.id), ('no_build', '=', False)]

            filter_mode = request.httprequest.cookies.get('filter_mode', False)
            if filter_mode == 'sticky':
                domain.append(('sticky', '=', True))
            elif filter_mode == 'nosticky':
                domain.append(('sticky', '=', False))

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

            category_id = int(request.httprequest.cookies.get('category') or 0) or request.env['ir.model.data'].xmlid_to_res_id('runbot.default_category')

            trigger_display = request.httprequest.cookies.get('trigger_display_%s' % project.id, None)
            if trigger_display is not None:
                trigger_display = [int(td) for td in trigger_display.split('-') if td]
            print(trigger_display)
            bundles = bundles.with_context(category_id=category_id)

            triggers = env['runbot.trigger'].search([('project_id', '=', project.id)])
            context.update({
                'active_category_id': category_id,
                'bundles': bundles,
                'project': project,
                'triggers': triggers,
                'trigger_display': trigger_display,
            })

        context.update({'message': request.env['ir.config_parameter'].sudo().get_param('runbot.runbot_message')})
        res = request.render('runbot.bundles', context)
        return res

    @route([
        '/runbot/bundle/<model("runbot.bundle"):bundle>',
        '/runbot/bundle/<model("runbot.bundle"):bundle>/page/<int:page>'
        ], website=True, auth='public', type='http')
    def bundle(self, bundle=None, page=1, limit=50, **kwargs):
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
            'project': bundle.project_id
            }

        return request.render('runbot.bundle', context)

    @o_route([
        '/runbot/bundle/<model("runbot.bundle"):bundle>/force',
    ], type='http', auth="user", methods=['GET', 'POST'], csrf=False)
    def force_bundle(self, bundle, **post):
        _logger.info('user %s forcing bundle %s', request.env.user.name, bundle.name) # user must be able to read bundle
        batch = bundle.sudo()._force()
        return werkzeug.utils.redirect('/runbot/batch/%s' % batch.id)

    @route(['/runbot/batch/<int:batch_id>'], website=True, auth='public', type='http')
    def batch(self, batch_id=None, **kwargs):
        batch = request.env['runbot.batch'].browse(batch_id)
        context = {
            'batch': batch,
            'project': batch.bundle_id.project_id,
        }
        return request.render('runbot.batch', context)

    @route(['/runbot/commit/<model("runbot.commit"):commit>'], website=True, auth='public', type='http')
    def commit(self, commit=None, **kwargs):
        context = {
            'commit': commit,
            'project': commit.repo_id.project_id,
            'reflogs': request.env['runbot.ref.log'].search([('commit_id', '=', commit.id)])
        }
        return request.render('runbot.commit', context)

    @o_route([
        '/runbot/build/<int:build_id>/<operation>',
    ], type='http', auth="public", methods=['POST'], csrf=False)
    def build_operations(self, build_id, operation, exact=0, **post):
        build = request.env['runbot.build'].sudo().browse(build_id)
        if operation == 'rebuild':
            build = build._rebuild()
        elif operation == 'kill':
            build._ask_kill()
        elif operation == 'wakeup':
            build._wake_up()

        return werkzeug.utils.redirect(build.build_url)

    @route(['/runbot/build/<int:build_id>'], type='http', auth="public", website=True)
    def build(self, build_id, search=None, **post):
        """Events/Logs"""

        Build = request.env['runbot.build']

        build = Build.browse([build_id])[0]
        if not build.exists():
            return request.not_found()

        context = {
            'build': build,
            'default_category': request.env['ir.model.data'].xmlid_to_res_id('runbot.default_category'),
            'project': build.params_id.trigger_id.project_id,
        }
        return request.render("runbot.build", context)

    @route('/runbot/glances', type='http', auth='public', website=True)
    def glances(self, **kwargs):
        bundles = request.env['runbot.bundle'].search([('sticky', '=', True)]) # NOTE we dont filter on project
        pending = self._pending()
        qctx = {
            'pending_total': pending[0],
            'pending_level': pending[1],
            'bundles': bundles,
        }
        return request.render("runbot.glances", qctx)

    @route(['/runbot/monitoring',
            '/runbot/monitoring/<int:category_id>',
            '/runbot/monitoring/<int:category_id>/<int:view_id>'], type='http', auth='user', website=True)
    def monitoring(self, category_id=None, view_id=None, **kwargs):
        pending = self._pending()
        hosts_data = request.env['runbot.host'].search([])
        if category_id:
            category = request.env['runbot.trigger.category'].browse(category_id)
            assert category.exists()
        else:
            category = request.env.ref('runbot.nightly_category')
            category_id = category.id
        bundles = request.env['runbot.bundle'].search([('sticky', '=', True)]) # NOTE we dont filter on project
        qctx = {
            'category': category,
            'pending_total': pending[0],
            'pending_level': pending[1],
            'scheduled_count': pending[2],
            'bundles': bundles,
            'hosts_data': hosts_data,
            'auto_tags': request.env['runbot.build.error'].disabling_tags(),
            'build_errors': request.env['runbot.build.error'].search([('random', '=', True)]),
            'kwargs': kwargs
        }
        return request.render(view_id if view_id else "runbot.monitoring", qctx)

    @route(['/runbot/config/<int:config_id>',
            '/runbot/config/<config_name>'], type='http', auth="public", website=True)
    def config(self, config_id=None, config_name=None, **kwargs):

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
            'last_monitored': last_monitored,  # nightly
            'kwargs': kwargs
        }
        return request.render(config.monitoring_view_id.id or "runbot.config_monitoring", qctx)
