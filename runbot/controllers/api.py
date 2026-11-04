import json

from odoo.http import Controller, Response, request
from odoo.http import route


def _parse_ids(ids):
    result = []
    for sid in ids.split(','):
        sid = sid.strip()
        if sid.isdigit():
            result.append(int(sid))
    return result


def _json_response(data, status=200):
    return Response(json.dumps(data, default=str), status=status, mimetype='application/json')


def _max_records():
    return int(request.env['ir.config_parameter'].sudo().get_param('runbot.api_max_records', 50))


def _check_max_records(count):
    max_records = _max_records()
    if count > max_records:
        return _json_response({'error': f'Too many records requested ({count}), maximum is {max_records}'}, status=400)
    return None


def _serialize_commit_link(commit_link):
    return {
        'repo': commit_link.commit_id.repo_id.name,
        'sha': commit_link.commit_id.name,
    }


def _serialize_log(log):
    return {
        'id': log.id,
        'create_date': log.create_date,
        'level': log.level,
        'type': log.type,
        'message': log.message,
        'func': log.func,
        'path': log.path,
        'line': log.line,
    }


def _serialize_batch_log(log):
    return {
        'id': log.id,
        'create_date': log.create_date,
        'level': log.level,
        'message': log.message,
    }


def _serialize_log_files(build):
    if not build.host or not build.log_list:
        return []
    base_url = build._http_log_url()
    return [{'name': name, 'url': f'{base_url}{name}.txt'} for name in build.log_list.split(',') if name]


def _serialize_databases(build):
    if not build.host:
        return []
    base_url = build._http_log_url()
    return [{
        'name': database.db_suffix,
        'db_name': database.name,
        'url': f'{base_url}{database.name}.zip',
    } for database in build.database_ids]


LOG_MODES = ('errors', 'all', 'none')


def _get_build_logs(build, logs):
    if logs == 'all':
        return build.sudo().log_ids
    if logs == 'errors':
        return build.sudo().error_log_ids
    return build.env['ir.logging']


def _serialize_build(build, depth=1, logs='errors'):
    children = []
    if depth > 0:
        for child in build.children_ids:
            children.append({
                **_serialize_build(child, depth=depth - 1, logs=logs),
                'relation': 'child',
            })
        for link in build.child_link_ids:
            children.append({
                **_serialize_build(link.child_id, depth=depth - 1, logs=logs),
                'relation': link.link_type,
                'link_orphan_result': link.orphan_result,
            })

    return {
        'id': build.id,
        'description': build.description,
        'display_name': build.display_name,
        'host': build.host,
        'trigger': build.trigger_id.name,
        'config': build.config_id.name,
        'version': build.version_id.name,
        'parent_id': build.parent_id.id or False,
        'create_batch_id': build.create_batch_id.id or False,
        'orphan_result': build.orphan_result,
        'state': {
            'local_state': build.local_state,
            'global_state': build.global_state,
            'local_result': build.local_result,
            'global_result': build.global_result,
        },
        'build_start': build.build_start,
        'build_end': build.build_end,
        'build_time': build.build_time,
        'commits': [_serialize_commit_link(cl) for cl in build.params_id.commit_link_ids],
        'logs': [_serialize_log(log) for log in _get_build_logs(build, logs)],
        'log_files': _serialize_log_files(build),
        'databases': _serialize_databases(build),
        'children': children,
    }


def _serialize_batch(batch, depth=1, logs='errors'):
    return {
        'id': batch.id,
        'bundle_id': batch.bundle_id.id,
        'bundle_name': batch.bundle_id.name,
        'category': batch.category_id.name,
        'create_date': batch.create_date,
        'state': batch.state,
        'result': batch._get_global_result(),
        'commits': [_serialize_commit_link(cl) for cl in batch.commit_link_ids],
        'logs': [_serialize_batch_log(log) for log in batch.log_ids],
        'builds': [_serialize_build(slot.build_id, depth=depth, logs=logs) for slot in batch.slot_ids if slot.build_id],
    }


def _serialize_bundle(bundle):
    return {
        'id': bundle.id,
        'name': bundle.name,
        'project_id': bundle.project_id.id,
        'project_name': bundle.project_id.name,
        'version': bundle.version_id.name,
        'sticky': bundle.sticky,
        'is_base': bundle.is_base,
        'last_batch_id': bundle.last_batch.id or False,
        'batches': [{
            'id': batch.id,
            'state': batch.state,
            'result': batch._get_global_result(),
            'create_date': batch.create_date,
        } for batch in bundle.batch_ids.sorted('id', reverse=True)[:40]],
    }


class RunbotApi(Controller):

    @route(['/runbot/api'], type='http', auth='public', website=False, csrf=False, sitemap=False)
    def api_documentation(self, **kwargs):
        doc = f"""# Runbot API

Read-only JSON API of the runbot frontend.
A bundle is a branch name across repos, a batch is one run of it on a set of commits,
a build is one test of a batch (one per trigger) and can have children builds.

## Routes

- `/runbot/api/bundle/<ids_or_names>`: bundles by id, slug (`master-1`) or name (`master`), with their 40 last batches.
- `/runbot/api/batch/<ids>?depth=1&logs=errors`: batches with their commits, logs and builds.
- `/runbot/api/build/<ids>?depth=1&logs=errors`: builds with their state, commits, logs, log_files, databases and children.

## Conventions

- Endpoints take a comma separated list of at most {_max_records()} values and return a JSON list.
- Records reference each other by id, fetch them with the matching route.
- Dates are UTC.

## Parameters

- `depth`: levels of children to include (0: none, default 1).
  Children past the depth have an empty `children` list, fetch them by id.
- `logs`: build log lines to include: `errors` (warnings and errors, default), `all` or `none`.
  The full text logs are linked in `log_files`.

## Values

- states: pending, testing, waiting, running, done
- results: ok, warn, ko, skipped, killed
- `global_state`/`global_result` include the children, `local_state`/`local_result` do not.
"""
        return Response(doc, status=200, mimetype='text/plain')

    @route(['/runbot/api/build/<string:ids>'], type='http', auth='public', website=False, csrf=False, sitemap=False)
    def api_build(self, ids, depth=1, logs='errors', **kwargs):
        build_ids = _parse_ids(ids)
        if error := _check_max_records(len(build_ids)):
            return error
        builds = request.env['runbot.build'].browse(build_ids).exists()
        if logs not in LOG_MODES:
            return _json_response({'error': f'logs must be one of {", ".join(LOG_MODES)}'}, status=400)
        depth = int(depth)
        return _json_response([_serialize_build(build, depth=depth, logs=logs) for build in builds])

    @route(['/runbot/api/batch/<string:ids>'], type='http', auth='public', website=False, csrf=False, sitemap=False)
    def api_batch(self, ids, depth=1, logs='errors', **kwargs):
        batch_ids = _parse_ids(ids)
        if error := _check_max_records(len(batch_ids)):
            return error
        batches = request.env['runbot.batch'].browse(batch_ids).exists()
        if logs not in LOG_MODES:
            return _json_response({'error': f'logs must be one of {", ".join(LOG_MODES)}'}, status=400)
        depth = int(depth)
        return _json_response([_serialize_batch(batch, depth=depth, logs=logs) for batch in batches])

    @route(['/runbot/api/bundle/<path:ids_or_names>'], type='http', auth='public', website=False, csrf=False, sitemap=False)
    def api_bundle(self, ids_or_names, **kwargs):
        values = [value.strip() for value in ids_or_names.split(',') if value.strip()]
        if error := _check_max_records(len(values)):
            return error
        Bundle = request.env['runbot.bundle']
        ids = []
        names = []
        for value in values:
            # ids and slugs win over names since a name is only unique per project:
            # a bundle named like a slug ("master-1") can only be fetched by its own id
            _slug, record_id = request.env['ir.http']._unslug(value)
            if record_id is not None:
                if record_id > 0:
                    ids.append(record_id)
            else:
                names.append(value)
        bundles = Bundle.browse(ids).exists()
        if names:
            bundles |= Bundle.search([('name', 'in', names)], order='id')
        return _json_response([_serialize_bundle(bundle) for bundle in bundles])
