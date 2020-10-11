# -*- coding: utf-8 -*-
import logging

from .common import RunbotCase

_logger = logging.getLogger(__name__)


class TestDockerfile(RunbotCase):

    def test_dockerfile_base_fields(self):
        dockerfile = self.env['runbot.dockerfile'].create({
            'name': 'Ubuntu Bionic (18.0)[Chrome 80]',
            'base_from': 'ubuntu:bionic',
            'is_default': True
        })

        self.assertEqual(dockerfile.image_tag, 'odoo:UbuntuBionic18.0Chrome80')

        self.env['runbot.dockerfile'].create({
            'name': 'Ubuntu 20.0',
            'base_from': 'ubuntu:focal'
        })

        self.assertEqual(self.env['runbot.dockerfile'].get_default(), dockerfile)

        base_step_id = self.env['runbot.dockerfile.step'].create({
            'name': 'Install base files',
            'content': """ENV LANG C.UTF-8
USER root
RUN set-x; apt-get update && apt-get install -y --no-install recommends apt-transport-https
            """
        })

        install_chrome_step = self.env['runbot.dockerfile.step'].create({
            'name': 'Install chrome',
            'content': 'RUN apt-get install google-chrome'
        })

        dockerfile.docker_step_order_ids += self.env['runbot.dockerfile.step.order'].create({
            'sequence': 10,
            'dockerfile_id': dockerfile.id,
            'docker_step_id': base_step_id.id
        })

        dockerfile.docker_step_order_ids += self.env['runbot.dockerfile.step.order'].create({
            'sequence': 20,
            'dockerfile_id': dockerfile.id,
            'docker_step_id': install_chrome_step.id
        })

        self.assertTrue(dockerfile.dockerfile.startswith('FROM ubuntu:bionic'))
        self.assertIn('USER root', dockerfile.dockerfile)
        self.assertIn('install google-chrome', dockerfile.dockerfile)
