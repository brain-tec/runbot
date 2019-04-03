# -*- coding: utf-8 -*-

from odoo.addons.runbot.controllers.frontend import Runbot


class RunbotJobs(Runbot):

    def build_info(self, build):
        res = super(RunbotJobs, self).build_info(build)
        res['parse_job_ids'] = [elmt.name for elmt in build.repo_id.parse_job_ids]
        return res
