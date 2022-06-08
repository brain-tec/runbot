# -*- coding: utf-8 -*-
import logging

from odoo.http import Controller, request, route

try:
    from odoo.addons.saas_worker.util import from_role
except ImportError:
    def from_role(_):
        return lambda _: None

_logger = logging.getLogger(__name__)
class MergebotReviewerProvisioning(Controller):
    @from_role('accounts')
    @route('/runbot_merge/users', type='json', auth='public')
    def list_users(self):
        env = request.env(su=True)
        return [{
                'github_login': u.github_login,
                'email': u.email,
            }
            for u in env['res.users'].search([])
        ]

    @from_role('accounts')
    @route('/runbot_merge/provision', type='json', auth='public')
    def provision_user(self, users):
        _logger.info('Provisioning %s users: %s.', len(users), ', '.join(map(
            '{email} ({github_login})'.format_map,
            users
        )))
        env = request.env(su=True)
        Partners = env['res.partner']
        Users = env['res.users']

        existing_partners = Partners.search([
            '|', ('email', 'in', [u['email'] for u in users]),
                 ('github_login', 'in', [u['github_login'] for u in users])
        ])
        _logger.info("Found %d existing matching partners.", len(existing_partners))
        partners = {}
        for p in existing_partners:
            if p.email:
                # email is not unique, though we want it to be (probably)
                current = partners.get(p.email)
                if current:
                    _logger.warning(
                        "Lookup conflict: %r set on two partners %r and %r.",
                        p.email, current.display_name, p.display_name,
                    )
                else:
                    partners[p.email] = p

            if p.github_login:
                # assume there can't be an existing one because github_login is
                # unique, and should not be able to collide with emails
                partners[p.github_login] = p

        if 'oauth_provider_id' in Users:
            odoo_provider = env.ref('auth_oauth.provider_openerp', False)
            if odoo_provider:
                for new in users:
                    if 'sub' in new:
                        new['oauth_provider_id'] = odoo_provider.id
                        new['oauth_uid'] = new.pop('sub')

        to_create = []
        created = updated = 0
        for new in users:
            # prioritise by github_login as that's the unique-est point of information
            current = partners.get(new['github_login']) or partners.get(new['email']) or Partners
            # entry doesn't have user -> create user
            if not current.user_ids:
                new['login'] = new['email']
                # entry has partner -> create user linked to existing partner
                # (and update partner implicitly)
                if current:
                    new['partner_id'] = current.id
                to_create.append(new)
                continue

            # otherwise update user (if there is anything to update)
            user = current.user_ids
            if len(user) != 1:
                _logger.warning("Got %d users for partner %s.", len(user), current.display_name)
                user = user[:1]
            update_vals = {
                k: v
                for k, v in new.items()
                if v not in ('login', 'email')
                if v != (user[k] if k != 'oauth_provider_id' else user[k].id)
            }
            if update_vals:
                user.write(update_vals)
                updated += 1
        if to_create:
            Users.create(to_create)
            created = len(to_create)

        _logger.info("Provisioning: created %d updated %d.", created, updated)
        return [created, updated]

    @from_role('accounts')
    @route(['/runbot_merge/get_reviewers'], type='json', auth='public')
    def fetch_reviewers(self, **kwargs):
        reviewers = request.env['res.partner.review'].sudo().search([
            '|', ('review', '=', True), ('self_review', '=', True)
        ]).mapped('partner_id.github_login')
        return reviewers

    @from_role('accounts')
    @route(['/runbot_merge/remove_reviewers'], type='json', auth='public', methods=['POST'])
    def update_reviewers(self, github_logins, **kwargs):
        partners = request.env['res.partner'].sudo().search([('github_login', 'in', github_logins)])
        partners.write({
            'review_rights': [(5, 0, 0)],
            'delegate_reviewer': [(5, 0, 0)],
        })

        # Assign the linked users as portal users
        partners.mapped('user_ids').write({
            'groups_id': [(6, 0, [request.env.ref('base.group_portal').id])]
        })
