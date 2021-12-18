# -*- coding: utf-8 -*-

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class Hook(http.Controller):

    @http.route(['/runbot/hook/<int:remote_id>'], type='http', auth="public", website=True, csrf=False)
    def hook(self, remote_id=None, **_post):
        event = request.httprequest.headers.get("X-Github-Event")
        payload = json.loads(request.params.get('payload', '{}'))
        if remote_id is None:
            repo_data = payload.get('repository')
            if repo_data and event in ['push', 'pull_request']:
                remote_domain = [
                    '|', '|', ('name', '=', repo_data['ssh_url']),
                    ('name', '=', repo_data['clone_url']),
                    ('name', '=', repo_data['clone_url'].rstrip('.git')),
                ]
                remote = request.env['runbot.remote'].sudo().search(
                    remote_domain, limit=1)
                remote_id = remote.id
        if not remote_id:
            return

        request.env['runbot.hook'].sudo().create({
            'payload': payload,
            'github_event': event,
            'remote_id': remote_id
        })

        return ""
