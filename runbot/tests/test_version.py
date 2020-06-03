# -*- coding: utf-8 -*-
from .common import RunbotCase


class TestVersion(RunbotCase):

    def test_basic_version(self):

        major_version = self.Version.create({'name': '12.0'})
        self.assertEqual(major_version.number, '12.00')
        self.assertTrue(major_version.is_major)

        saas_version = self.Version.create({'name': 'saas-12.1'})
        self.assertEqual(saas_version.number, '12.01')
        self.assertFalse(saas_version.is_major)

        self.assertGreater(saas_version.number, major_version.number)

        master_version = self.Version.create({'name': 'master'})
        self.assertEqual(master_version.number, '~')
        self.assertGreater(master_version.number, saas_version.number)
