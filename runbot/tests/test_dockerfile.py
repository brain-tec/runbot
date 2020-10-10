# -*- coding: utf-8 -*-
import logging

from .common import RunbotCase

_logger = logging.getLogger(__name__)


class TestDockerfile(RunbotCase):

    def test_dockerfile_base_fields(self):
        xml_content = """
        <t t-name="runbot.docker_base">
        <t t-set="docker_from" t-value="docker_from or 'ubuntu:focal'"/>
        <t t-call="runbot.docker_from"/>
        </t>"""

        focal_template = self.env['ir.ui.view'].create({
            'name': 'docker_focal_test',
            'type': 'qweb',
            'key': 'docker.docker_focal_test',
            'arch_db': xml_content
        })

        dockerfile = self.env['runbot.dockerfile'].create({
            'name': 'Ubuntu Focal (20.0)[Chrome 80]',
            'template_id': focal_template.id,
            'to_build': False
        })

        self.assertEqual(dockerfile.image_tag, 'odoo:UbuntuFocal20.0Chrome80')
        self.assertTrue(dockerfile.dockerfile.startswith('FROM ubuntu:focal'))
