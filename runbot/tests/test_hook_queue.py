import json

from odoo.tests.common import HttpCase

from .common import RunbotCase


class TestHookQueue(RunbotCase):

    def test_json_field(self):
        HookQueue = self.env['runbot.hook']
        hook_queue = HookQueue.create({'payload': {"action": "edited"}})
        self.assertEqual(hook_queue.payload, {'action': 'edited'}, "payload should return a valid python object")

        hook_queue = HookQueue.create({'payload': {}})
        self.assertEqual(hook_queue.payload, {}, "payload should return a valid python object")

class TestHookController(HttpCase):

    def setUp(self):
        super().setUp()
        self.project = self.env['runbot.project'].create({'name': 'Tests'})
        self.repo_server = self.env['runbot.repo'].create({
            'name': 'server',
            'project_id': self.project.id,
            'server_files': 'server.py',
            'addons_paths': 'addons,core/addons'
        })

        self.remote_server = self.env['runbot.remote'].create({
            'name': 'bla@example.com:base/server',
            'repo_id': self.repo_server.id,
            'token': '123',
        })

    def test_hook_controller(self):

        test_data = { 'payload' : r"""
            {
                "action": "opened",
                "number": 12345,
                "pull_request": {
                    "url": "https://www.example.com/repos/odoo/odoo/pulls/12345",
                    "number": 12345,
                    "state": "open",
                    "title": "[IMP]: Example request",
                    "user": { "login": "marcel" }
                }
            }
        """
        }

        res = self.url_open(f'/runbot/hook/{self.remote_server.id}', data=test_data, headers={'X-Github-Event': 'pull_request'})
        self.assertEqual(res.status_code, 200)

        latest_hook = self.env['runbot.hook'].search([('remote_id', '=', self.remote_server.id)], limit=1)
        self.assertTrue(latest_hook.exists())
        self.assertEqual(latest_hook.payload, json.loads(test_data['payload']))
        self.assertEqual(latest_hook.payload.get('pull_request').get('number'), 12345)
        self.assertEqual(latest_hook.github_event, 'pull_request')
