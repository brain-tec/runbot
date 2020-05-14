# -*- coding: utf-8 -*-
import logging
_logger = logging.getLogger(__name__)

def migrate(cr, version):
    # dependency is not correct since it will be all commits. This also free the name for a build dependant on another build params
    #cr.execute("ALTER TABLE runbot_build_dependency RENAME TO runbot_build_commit;")
    #cr.execute("ALTER SEQUENCE runbot_build_dependency_id_seq RENAME TO runbot_build_commit_id_seq")

    # Fix duplicate problems
    cr.execute("UPDATE runbot_build SET duplicate_id = null WHERE duplicate_id > id")
    cr.execute("UPDATE runbot_build SET local_state='done' WHERE duplicate_id IS NULL AND local_state = 'duplicate';")
    # Remove builds without a repo
    cr.execute("DELETE FROM runbot_build WHERE repo_id IS NULL")

    cr.execute("DELETE FROM ir_ui_view WHERE id IN (SELECT res_id FROM ir_model_data WHERE name = 'inherits_branch_in_menu' AND module = 'runbot')")

    # Fix branches
    cr.execute("""DELETE FROM runbot_branch WHERE name SIMILAR TO 'refs/heads/\d+' RETURNING id,name;""")  # Remove old bad branches named like PR
    for branch_id, name in cr.fetchall():
        _logger.warning('Deleting branch id %s with name "%s"', branch_id, name)

    cr.execute("""SELECT branch_name,repo_id, count(*) AS nb FROM runbot_branch GROUP BY branch_name,repo_id HAVING count(*) > 1;""")  # Branches with duplicate branch_name in same repo
    for branch_name, repo_id, nb in cr.fetchall():
        cr.execute("""DELETE FROM runbot_branch WHERE (sticky='f' OR sticky IS NULL) AND branch_name=%s and repo_id=%s and name ~ 'refs/heads/.+/.+' RETURNING id,branch_name;""", (branch_name, repo_id))
        for branch_id, branch_name in cr.fetchall():
            _logger.warning('Deleting branch id %s with branch_name "%s"', branch_id, branch_name)


    # TODO delete runbot.inherits_branch_in_menu

    # TODO empty runbot_repo_hooktime and reftime
    return
