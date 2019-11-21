# -*- coding: utf-8 -*-
{
    'name': 'migration bot',
    'summary': 'Migration bot that test migration one module at a time.',
    'author': 'Odoo SA',
    'version': '0.1',
    'depends': ['base', 'website'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/project.xml',
        'views/repo.xml',
        'data/migra_cron.xml'
    ],
}
