# -*- coding: utf-8 -*-
import logging

from .common import RunbotCase

_logger = logging.getLogger(__name__)


class TestDockerfile(RunbotCase):

    def test_dockerfile_base_fields(self):
        xml_content = """<t t-call="runbot.docker_base">
    <t t-set="custom_values" t-value="{
      'from': 'ubuntu:focal',
      'phantom': True,
      'additional_pip': 'babel==2.8.0',
      'chrome_version': '86.0.4240.183-1',
    }"/>
</t>
"""

        focal_template = self.env['ir.ui.view'].create({
            'name': 'docker_focal_test',
            'type': 'qweb',
            'key': 'docker.docker_focal_test',
            'arch_db': xml_content
        })

        dockerfile = self.env['runbot.dockerfile'].create({
            'name': 'Ubuntu Focal (20.0)[Chrome 86]',
            'template_id': focal_template.id,
            'to_build': False
        })

        self.assertEqual(dockerfile.image_tag, 'odoo:UbuntuFocal20.0Chrome86')
        self.assertTrue(dockerfile.dockerfile.startswith('FROM ubuntu:focal'))
        self.assertIn(' apt-get install -y -qq google-chrome-stable=86.0.4240.183-1', dockerfile.dockerfile)
        self.assertIn('# Install phantomjs', dockerfile.dockerfile)
        self.assertIn('pip install babel==2.8.0', dockerfile.dockerfile)
