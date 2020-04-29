# -*- coding: utf-8 -*-

from odoo.api import Environment
from odoo import SUPERUSER_ID
import logging
import progressbar
from collections import defaultdict
import datetime

def _bar(total):
    b = progressbar.ProgressBar(maxval=total, \
        widgets=[progressbar.Bar('=', '[', ']'), ' ', progressbar.Percentage()])
    b.start()
    return b

_logger = logging.getLogger(__name__)

def migrate(cr, version):
    env = Environment(cr, SUPERUSER_ID, {})
    # some checks:
    for keyword in ('real_build', 'duplicate_id', '_get_all_commit'):
        matches = env['runbot.build.config.step'].search([('python_code', 'like', keyword)])
        if matches:
            _logger.warning('Some python steps found with %s ref: %s', keyword, matches)

    cr.execute('SELECT id FROM runbot_repo WHERE nginx = true')
    if cr.fetchone():
        cr.execute("""INSERT INTO ir_config_parameter (KEY, value) VALUES ('runbot_nginx', 'True')""")

    ########################
    # Repo groups, triggers and projects
    ########################

    repo_to_group = {}
    owner_group_to_repo = {}

    RD_project = env['runbot.project'].create({
        'name': 'R&D'
    })
    security_project = env['runbot.project'].create({
        'name': 'Security'
    })
    project_matching = { # some hardcoded info 
        'odoo': RD_project,
        'enterprise': RD_project,
        'upgrade': RD_project,
        'design-themes': RD_project,
        'odoo-security': security_project,
        'enterprise-security': security_project,
    }
    cr.execute("""
        SELECT 
        id, name, duplicate_id, modules, modules_auto, server_files, manifest_files, addons_paths
        FROM runbot_repo order by id
    """)
    for id, name, duplicate_id, modules, modules_auto, server_files, manifest_files, addons_paths in cr.fetchall():
        cr.execute(""" SELECT res_groups_id FROM res_groups_runbot_repo_rel WHERE runbot_repo_id = %s""", (id,))
        group_ids = [r[0] for r in cr.fetchall()]
        repo_name = name.split('/')[-1].replace('.git', '')
        owner = name.split(':')[-1].split('/')[0]
        repo = env['runbot.repo'].browse(id)
        if duplicate_id in repo_to_group:
            repo.repo_group_id = repo_to_group[duplicate_id]
            repo_to_group[id] = repo_to_group[duplicate_id]
            # todo make some checks ?
        else:
            # if not, we need to give information on how to group repos: odoo+enterprise+upgarde+design-theme/se/runbot
            # this mean that we will need to group build too. Could be nice but maybe a little difficult.
            if repo_name in project_matching:
                project = project_matching[repo_name]
            else:
                project = env['runbot.project'].create({
                    'name': repo_name,
                })
            group = env['runbot.repo.group'].create({
                'name': repo_name,
                'project_id': project.id,
                #'main': id, # older repo should be the main, not sur it is usefull
                'modules': modules,
                'modules_auto': modules_auto,
                'group_ids': [(4, group_id) for group_id in group_ids],
                'server_files': server_files,
                'manifest_files': manifest_files,
                'addons_paths': addons_paths,
            })
            repo.repo_group_id = group
            repo_to_group[id] = group
        owner_group_to_repo[(owner, repo_to_group[id].id)] = id

    _logger.info('Creating triggers')
    processed = set()
    cr.execute("""
        SELECT 
        id, name, repo_config_id
        FROM runbot_repo order by id
    """)
    triggers = {}
    triggers_by_project = defaultdict(list)
    for id, name, repo_config_id in cr.fetchall():
        repo_name = name.split('/')[-1].replace('.git', '')
        cr.execute(""" SELECT dependency_id FROM runbot_repo_dep_rel WHERE dependant_id = %s""", (id,))
        dependency_ids = [r[0] for r in cr.fetchall()]
        group = repo_to_group[id]
        if group.id not in processed:
            processed.add(group.id)
            trigger = env['runbot.trigger'].create({
                'name': repo_name,
                'project_id': group.project_id.id,
                'repos_group_ids': [(4, group.id)],
                'dependency_ids': [(4, repo_to_group[dependency_id].id) for dependency_id in dependency_ids],
                'config_id': repo_config_id if repo_config_id else env.ref('runbot.runbot_build_config_default').id,
            })
            triggers[group.id] = trigger
            triggers_by_project[group.project_id.id].append(trigger)
        # TODO create trigger using dependency_ids

    # no build, config, ...

    ########################
    # Bundles
    ########################
    _logger.info('Creating bundles')

    branches = env['runbot.branch'].search([], order='id')

    branches._compute_reference_name()

    bundles = {}
    versions = {}
    branch_to_bundle = {}
    branch_to_version = {}
    progress = _bar(len(branches))
    for i, branch in enumerate(branches):
        progress.update(i)
        if branch.sticky and branch.branch_name not in versions:
            versions[branch.branch_name] = env['runbot.version'].create({
                'name': branch.branch_name,
            })
        group = branch.repo_id.repo_group_id
        if branch.target_branch_name and branch.pull_head_name:
            # 1. update source_repo: do not call github and use a naive approach:
            # pull_head_name contains odoo-dev and a repo in group starts with odoo-dev -> this is a known repo.
            owner = branch.pull_head_name.split(':')[0]
            pull_head_repo_id = owner_group_to_repo.get((owner, group.id))
            if pull_head_repo_id:
                branch.pull_head_repo_id = pull_head_repo_id
        project_id = group.project_id
        name = branch.reference_name

        key = (name, project_id)
        if key not in bundles:
            bundle = env['runbot.bundle'].create({
                'name': name,
                'project_id': project_id.id,
                'sticky': branch.sticky,
                'is_base': branch.sticky,
                'version_id': next((version.id for k, version in versions.items() if (
                    k == branch.target_branch_name or \
                    branch.branch_name.startswith(k)
                )), next(version.id for k, version in versions.items() if k=='master'))
            })
            bundles[key] = bundle
        bundle = bundles[key]
        branch.bundle_id = bundle
        branch_to_bundle[branch.id] = bundle
        branch_to_version[branch.id] = bundle.version_id.id

    branches.flush()
    env['runbot.bundle'].flush()
    progress.finish()

    batch_size = 100000

    sha_commits = {}
    sha_repo_commits = {}
    branch_heads = {}
    build_commit_ids = defaultdict(dict)
    cr.execute("SELECT count(*) FROM runbot_build")
    nb_build = cr.fetchone()[0]

    ########################
    # BUILDS
    ########################
    _logger.info('Creating main commits')
    counter = 0
    progress = _bar(nb_build)
    cross_project_duplicate_ids = []
    for offset in range(0, nb_build, batch_size):
        cr.execute("""
            SELECT id,
            repo_id, name, author, author_email, committer, committer_email, subject, date, duplicate_id, branch_id
            FROM runbot_build ORDER BY id asc LIMIT %s OFFSET %s""", (batch_size, offset))

        for id ,repo_id, name, author, author_email, committer, committer_email, subject, date, duplicate_id, branch_id in cr.fetchall():
            progress.update(counter)
            if not repo_id:
                _logger.warning('No repo_id for build %s, skipping', id)
                continue
            group = repo_to_group[repo_id]
            group_id = group.id
            key = (name, group_id)
            if key in sha_repo_commits:
                commit = sha_repo_commits[key]
            else:
                if duplicate_id and group.project_id.id != RD_project.id:
                    cross_project_duplicate_ids.append(id)
                elif duplicate_id:
                    _logger.warning('Problem: duplicate: %s,%s', id, duplicate_id)

                commit = env['runbot.commit'].create({
                    'name': name,
                    'repo_group_id': group_id,
                    'author': author,
                    'author_email': author_email,
                    'committer': committer,
                    'committer_email': committer_email,
                    'subject': subject,
                    'date': date
                })
                sha_repo_commits[key] = commit
                sha_commits[name] = commit
            branch_heads[branch_id] = commit.id
            counter += 1

            build_commit_ids[id][commit.repo_group_id.id] = commit.id


    progress.finish()

    _logger.info('Cleaning cross project duplicates')
    cr.execute("UPDATE runbot_build SET local_state='done', duplicate_id=NULL WHERE id IN %s", (tuple(cross_project_duplicate_ids), ))

    _logger.info('Creating params')
    counter = 0

    cr.execute("SELECT count(*) FROM runbot_build WHERE duplicate_id IS NULL")
    nb_real_build = cr.fetchone()[0]
    progress = _bar(nb_real_build)

    #monkey patch to avoid search
    original = env['runbot.build.params']._find_existing
    existing = {}
    def _find_existing(fingerprint):
        return existing.get(fingerprint, env['runbot.build.params'])

    param = env['runbot.build.params']
    param._find_existing = _find_existing

    for offset in range(0, nb_real_build, batch_size):
        progress.update(counter)
        counter+=1
        cr.execute("""
            SELECT
            id, branch_id, repo_id, extra_params, config_id, config_data, commit_path_mode
            FROM runbot_build WHERE duplicate_id IS NULL ORDER BY id asc LIMIT %s OFFSET %s""", (batch_size, offset))
        
        for id, branch_id, repo_id, extra_params, config_id, config_data, commit_path_mode in cr.fetchall():

            build_commit_ids_create_values = [
                {'commit_id': build_commit_ids[id][repo_to_group[repo_id].id],'repo_id': repo_id, 'match_type':'exact'}]

            cr.execute('SELECT dependency_hash, dependecy_repo_id, match_type FROM runbot_build_dependency WHERE build_id=%s', (id,))
            for dependency_hash, dependecy_repo_id, match_type in cr.fetchall():
                group_id = repo_to_group[dependecy_repo_id].id
                key = (dependency_hash, group_id)
                commit = sha_repo_commits.get(key) or sha_commits.get(dependency_hash) # TODO check this (changing repo)
                if not commit:
                    # -> most of the time, commit in exists but with wrong repo. Info can be found on other commit.
                    _logger.warning('Missing commit %s created', dependency_hash)
                    commit = env['runbot.commit'].create({
                        'name': dependency_hash,
                        'repo_group_id': group_id,
                    })
                    sha_repo_commits[key] = commit
                    sha_commits[dependency_hash] = commit
                build_commit_ids[id][commit.repo_group_id.id] = commit.id
                build_commit_ids_create_values.append({'commit_id': commit.id,'repo_id': dependecy_repo_id, 'match_type':match_type})

            params = param.create({
                'version_id':  branch_to_version[branch_id],
                'extra_params': extra_params,
                'config_id': config_id,
                'project_id': repo_to_group[repo_id].project_id,
                #'trigger_id': triggers[repo_to_group[repo_id].id].id,
                'config_data': config_data,
                'commit_path_mode':commit_path_mode,
                'commit_ids': [(0, 0, values) for values in build_commit_ids_create_values]
            })
            existing[params.fingerprint] = params
            cr.execute('UPDATE runbot_build SET params_id=%s WHERE id=%s OR duplicate_id = %s', (params.id, id, id))
            # TODO one dev pass to check if params are the same for duplicate?
            # TODO deps from logs?
        env.cache.invalidate()
    progress.finish()

    env['runbot.build.params']._find_existing = original


    for branch, head in branch_heads.items():
        cr.execute('UPDATE runbot_branch SET head=%s WHERE id=%s', (head, branch))
    del branch_heads
    # adapt build commits


    _logger.info('Creating batchs')
    ###################
    # Bundle batch
    ####################
    cr.execute("SELECT count(*) FROM runbot_build WHERE parent_id IS NOT NULL")
    nb_root_build = cr.fetchone()[0]
    counter = 0
    progress = _bar(nb_root_build)
    previous_batch = {}
    for offset in range(0, nb_root_build, batch_size):
        cr.execute("""
            SELECT
            id, duplicate_id, repo_id, branch_id, create_date, build_type, config_id
            FROM runbot_build WHERE parent_id IS NULL order by id asc
            LIMIT %s OFFSET %s""", (batch_size, offset))
        for id, duplicate_id, repo_id, branch_id, create_date, build_type, config_id in cr.fetchall():
            progress.update(counter)
            counter += 1
            if repo_id is None:
                _logger.warning('Skipping %s: no repo', id)
                continue
            bundle = branch_to_bundle[branch_id]
            # try to merge build in same batch
            # not temporal notion in this case, only hash consistency
            batch = False
            build_id = duplicate_id or id
            build_commits = build_commit_ids[build_id]
            batch_group_repos_ids = []
            
            # check if this build can be added to last_batch
            if bundle.last_batch:
                if create_date - bundle.last_batch.last_update < datetime.timedelta(minutes=5):
                    if duplicate_id and build_id in bundle.last_batch.slot_ids.mapped('build_id').ids:
                        continue

                    # to fix: nightly will be in the same batch of the previous normal one. If config_id is diffrent, create batch?
                    # possible fix: max create_date diff
                    batch = bundle.last_batch
                    batch_commits = batch.bundle_commit_ids.mapped('commit_id')
                    batch_group_repos_ids = batch_commits.mapped('repo_group_id').ids
                    for commit in batch_commits:
                        repo_group_id = commit.repo_group_id.id
                        if repo_group_id in build_commits:
                            if commit.id != build_commits[repo_group_id]:
                                batch = False
                                batch_group_repos_ids = []
                                break

            missing_commits = [commit_id for repo_group_id, commit_id in build_commits.items() if repo_group_id not in batch_group_repos_ids]
            triggers[repo_to_group[repo_id].id].id
            #if trigger.config_id != 
            if not batch:
                batch = env['runbot.batch'].create({
                    'create_date': create_date,
                    'last_update': create_date,
                    'state': 'ready',
                    'bundle_id': bundle.id
                })
                #if bundle.last_batch:
                #    previous = previous_batch.get(bundle.last_batch.id)
                #    if previous:
                #        previous_build_by_trigger = {slot.trigger_id.id: slot.build_id.id for slot in previous.slot_ids}
                #    else:
                #        previous_build_by_trigger = {}
                #    batch_slot_triggers = bundle.last_batch.slot_ids.mapped('trigger_id').ids
                #    missing_trigger_ids = [trigger for trigger in triggers_by_project[bundle.project_id.id] if trigger.id not in batch_slot_triggers]
                #    for trigger in missing_trigger_ids:
                #        env['runbot.batch.slot'].create({
                #            'trigger_id': trigger.id,
                #            'batch_id': bundle.last_batch.id,
                #            'build_id': previous_build_by_trigger.get(trigger.id), # may be None, if we want to create empty slots. Else, iter on slot instead
                #            'link_type': 'matched',
                #            'active': True,
                #        })

                previous_batch[batch.id] = bundle.last_batch
                bundle.last_batch = batch
            else:
                batch.last_update = create_date
            env['runbot.batch.slot'].create({
                'trigger_id': triggers[repo_to_group[repo_id].id].id,
                'batch_id': batch.id,
                'build_id': build_id,
                'link_type': 'rebuild' if build_type == 'rebuild' else 'matched' if duplicate_id else 'created',
                'active': True,
            })
            for missing_commit in missing_commits: # todo improve this, need time to prefetch params + commits
                env['runbot.batch.commit'].create({
                    'commit_id': missing_commit,
                    'batch_id': batch.id,
                    'match_type': 'head', # TODO fixme
                    #'has_main' = True, ?
                })

        env.cache.invalidate()
    progress.finish()

    #Build of type rebuild may point to same params as rebbuild?

    ###################
    # Cleaning (performances)
    ###################
    # 1. avoid UPDATE "runbot_build" SET "commit_path_mode"=NULL WHERE "commit_path_mode"='soft'

    _logger.info('Pre-cleaning')
    cr.execute('alter table runbot_build alter column commit_path_mode drop not null')
    cr.execute('ANALYZE')
    cr.execute("delete from runbot_build where local_state='duplicate'") # what about duplicate childrens?
    _logger.info('End')

    # todo rename folders from dest to id.
