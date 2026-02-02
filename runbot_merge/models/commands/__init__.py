import dataclasses
import typing
from collections.abc import Iterable, Iterator
from enum import Enum

from odoo import fields, models
from odoo.addons.base.models.res_partner import Partner
from odoo.api import Environment
from odoo.tools import SQL, lazy_property
from .commands import Command
from ..utils import enum

if typing.TYPE_CHECKING:
    from ..pull_requests import PullRequests
else:
    PullRequests = typing.Any


class AccessFailure(Exception):
    pass


@dataclasses.dataclass(kw_only=True)
class Rel:
    author: Partner
    pr: PullRequests

    @property
    def env(self) -> Environment:
        return self.author.env

    @lazy_property
    def is_admin(self) -> bool:
        return self.env['res.partner.review'].search_count([
            ('partner_id', '=', self.author.id),
            ('repository_id', '=', self.pr.repository.id),
            ('review', '=', True) if self.pr.author != self.author else ('self_review', '=', True),
        ]) == 1

    @lazy_property
    def is_reviewer(self) -> bool:
        return self.is_admin \
            or self.pr.source_id and self.pr.source_id in self.author.delegate_reviewer \
            or any(p in self.author.delegate_reviewer for p in self.pr._iter_ancestors())

    @lazy_property
    def is_author(self) -> bool:
        return self.pr.author == self.author or self.is_reviewer

    @lazy_property
    def source_author(self) -> bool:
        return bool(self.pr.source_id) \
            and Rel(author=self.author, pr=self.pr.source_id).is_author

    @lazy_property
    def is_employee(self) -> bool:
        return (self.is_author or self.source_author) \
            and self.author.user_ids \
            and self.author.user_ids.has_group('base.group_user')

    @lazy_property
    def super_admin(self) -> bool:
        return self.is_admin \
            and self.author.user_ids \
            and self.author.user_ids.has_group('runbot_merge.group_admin')

    @lazy_property
    def can_override(self) -> bool:
        return any(
            not r.repository_id or (self.pr.repository in r.repository_id)
            for r in self.author.override_rights
        )

def commands_list() -> Iterable[tuple[str, str]]:
    for command in Command.__args__:
        if issubclass(command, Enum):
            for cmd in command:
                name = f"{command.__name__}.{cmd.name}"
                yield name, name
        else:
            yield command.__name__, command.__name__

class ACL(models.Model):
    _name = 'runbot_merge.acls'
    _description = "Mergebot non-user access control"

    command = fields.Selection(list(commands_list()), required=True)
    arg = fields.Char()

    effect = fields.Selection(
        [("add", "Add"), ("remove", "Remove")],
        required=True,
        column_type=enum(_name, 'state'),
    )

    predicate = fields.Char(
        help="Command predicate, receives a `rel` object exposing the `author`"
             " of the comment, the `pr` being commented on, and various"
             " properties indicating the relationships between the two.",
    )

    partner_id = fields.Many2one('res.partner')
    repository_id = fields.Many2one('runbot_merge.repository')

    def _auto_init(self):
        for field in self._fields.values():
            if not isinstance(field, fields.Selection) or field.column_type[0] == 'varchar':
                continue

            t = field.column_type[1]
            self.env.cr.execute("SELECT 1 FROM pg_type WHERE typname = %s", [t])
            if not self.env.cr.rowcount:
                self.env.cr.execute(
                    f"CREATE TYPE {t} AS ENUM %s",
                    [tuple(s for s, _ in field.selection)]
                )

        super()._auto_init()

        # support for looking up records with by partner and repository, `add` first.
        self.env.cr.execute(SQL(
            "CREATE UNIQUE INDEX %s ON %s"
            " (effect, partner_id, repository_id, command, arg)"
            " NULLS NOT DISTINCT",
            SQL.identifier(f"{self._table}_main_index"),
            SQL.identifier(self._table),
        ))

    def help(self) -> Iterator[tuple[str, str]]:
        cmds = set()
        for acl in self:
            if acl.effect == 'add':
                cmds.add(acl.command)
                if '.' in acl.command:
                    cmds.add(acl.command.split('.', 1)[0])
            else:
                cmds.discard(acl.command)

        for Cmd in commands.Command.__args__:
            if Cmd.__name__ in cmds:
                yield from Cmd.help(cmds)

    def commands_check(self, cmds: Iterable[Command]) -> Iterator[Command]:
        acls = set()
        for acl in self:
            acl_command = acl.command
            if acl.arg:
                acl_command += f'[{acl.arg}]'
            if acl.effect == 'add':
                acls.add(acl_command)
            else:
                acls.discard(acl_command)

        for command in cmds:
            if hasattr(command, 'checkacl'):
                has_access = command.checkacl(acls)
            elif isinstance(command, Enum):
                has_access = f"{type(command).__name__}.{command.name}" in acls
            else:
                has_access = type(command).__name__ in acls

            if not has_access:
                raise AccessFailure(command)
            yield command

