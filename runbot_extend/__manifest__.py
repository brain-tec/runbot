{
    'name':
    'Runbot Jobs',
    'category':
    'Website',
    'summary':
    'Runbot Jobs',
    'version':
    '2.0',
    'description':
    "Runbot Jobs",
    'author':
    'Odoo SA',
    'depends': ['runbot'],
    'data': [
        # 'data/runbot.job.csv',
        # 'data/runbot.regex.csv',
        'data/runbot_build_config_data.xml',
        'data/cron.xml',
        'views/repo.xml',
        # 'views/job.xml',
        # 'views/regex.xml',
        'views/templates.xml',
    ],
}
