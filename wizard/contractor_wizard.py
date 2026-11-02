# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HelpdeskContractorAssignWizard(models.TransientModel):
    _name = "helpdesk.contractor.assign.wizard"
    _description = "Assign a Contractor to a Helpdesk Ticket"

    ticket_id = fields.Many2one("helpdesk.ticket", required=True, readonly=True)
    contractor_id = fields.Many2one(
        "res.partner", string="Contractor / Vendor", required=True,
        help="Any contact or company with an email address. Contacts already used "
             "as contractors are listed first.")
    contractor_email = fields.Char(related="contractor_id.email", readonly=False,
                                   string="Contractor Email")
    user_id = fields.Many2one("res.users", string="Responsible",
                              default=lambda self: self.env.user, required=True)
    scope = fields.Text(string="Scope / Instructions")
    site_details = fields.Text(string="Site Details")
    send_now = fields.Boolean(string="Send for confirmation now", default=True)
    # Property: Compound > Building > Unit (saved back on the ticket)
    hd_compound_id = fields.Many2one("property.project", string="Compound")
    hd_building_id = fields.Many2one(
        "property.sub.project", string="Building",
        domain="[('property_project_id', '=?', hd_compound_id)]")
    hd_unit_id = fields.Many2one(
        "property.details", string="Unit / Flat",
        domain="[('subproject_id', '=?', hd_building_id), '|', "
               "('property_project_id', '=?', hd_compound_id), "
               "('subproject_id.property_project_id', '=?', hd_compound_id)]")
    hd_unit_floor = fields.Integer(related="hd_unit_id.floor", string="Floor")
    hd_tenant_id = fields.Many2one(related="hd_unit_id.hd_current_tenant_id", string="Tenant")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ticket = self.env["helpdesk.ticket"].browse(
            res.get("ticket_id") or self.env.context.get("default_ticket_id"))
        if ticket:
            res.setdefault("scope", ticket._contractor_default_scope())
            res.setdefault("site_details", ticket._contractor_default_site_details())
            res.setdefault("hd_compound_id", ticket.hd_compound_id.id)
            res.setdefault("hd_building_id", ticket.hd_building_id.id)
            res.setdefault("hd_unit_id", ticket.hd_unit_id.id)
        return res

    @api.onchange("hd_compound_id")
    def _onchange_hd_compound_id(self):
        if self.hd_building_id and self.hd_building_id.property_project_id != self.hd_compound_id:
            self.hd_building_id = False
        if self.hd_unit_id and (self.hd_unit_id.property_project_id
                                or self.hd_unit_id.subproject_id.property_project_id) != self.hd_compound_id:
            self.hd_unit_id = False
        self._hd_refresh_site_details()

    @api.onchange("hd_building_id")
    def _onchange_hd_building_id(self):
        if self.hd_building_id:
            self.hd_compound_id = self.hd_building_id.property_project_id
        if self.hd_unit_id and self.hd_unit_id.subproject_id != self.hd_building_id:
            self.hd_unit_id = False
        self._hd_refresh_site_details()

    @api.onchange("hd_unit_id")
    def _onchange_hd_unit_id(self):
        if self.hd_unit_id:
            self.hd_building_id = self.hd_unit_id.subproject_id
            self.hd_compound_id = self.hd_unit_id.property_project_id \
                or self.hd_unit_id.subproject_id.property_project_id
        self._hd_refresh_site_details()

    def _hd_refresh_site_details(self):
        if self.ticket_id:
            self.site_details = self.ticket_id._contractor_default_site_details(
                compound=self.hd_compound_id, building=self.hd_building_id, unit=self.hd_unit_id)

    def action_assign(self):
        self.ensure_one()
        if self.send_now and not self.contractor_id.email:
            raise UserError(_("Add an email address for %s to send the request.",
                              self.contractor_id.display_name))
        ticket_vals = {}
        for fname in ("hd_compound_id", "hd_building_id", "hd_unit_id"):
            if self[fname] != self.ticket_id[fname]:
                ticket_vals[fname] = self[fname].id or False
        if ticket_vals:
            self.ticket_id.write(ticket_vals)
        job = self.env["helpdesk.contractor.job"].create({
            "ticket_id": self.ticket_id.id,
            "contractor_id": self.contractor_id.id,
            "user_id": self.user_id.id,
            "scope": self.scope,
            "site_details": self.site_details,
        })
        if self.send_now:
            job.action_send_to_contractor()
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.contractor.job",
            "res_id": job.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }


class HelpdeskContractorRejectWizard(models.TransientModel):
    _name = "helpdesk.contractor.reject.wizard"
    _description = "Reject a Contractor Quotation or Work"

    job_id = fields.Many2one("helpdesk.contractor.job", required=True, readonly=True)
    stage = fields.Selection([("quotation", "Quotation"), ("work", "Work")],
                             required=True, readonly=True)
    reason = fields.Text(required=True, string="Reason / What to change",
                         help="Sent to the contractor.")

    def action_reject(self):
        self.ensure_one()
        if self.stage == "quotation":
            self.job_id._do_reject_quotation(self.reason)
        else:
            self.job_id._do_reject_work(self.reason)
        return {"type": "ir.actions.act_window_close"}
