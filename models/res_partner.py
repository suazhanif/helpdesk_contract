# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_contractor = fields.Boolean(
        string="Contractor / Vendor",
        help="Offered first when assigning a contractor to a helpdesk ticket. "
             "Set automatically the first time the contact is assigned.",
    )
    contractor_job_ids = fields.One2many(
        "helpdesk.contractor.job", "contractor_id", string="Contractor Jobs")
    contractor_job_count = fields.Integer(compute="_compute_contractor_job_count")

    @api.depends("contractor_job_ids")
    def _compute_contractor_job_count(self):
        for partner in self:
            partner.contractor_job_count = len(partner.contractor_job_ids)

    def action_view_contractor_jobs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "helpdesk_contractor.action_helpdesk_contractor_job")
        action["domain"] = [("contractor_id", "=", self.id)]
        action["context"] = {"default_contractor_id": self.id}
        return action
