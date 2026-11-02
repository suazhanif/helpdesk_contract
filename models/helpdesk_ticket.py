# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.tools import html2plaintext

from .contractor_job import STATES


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    contractor_job_ids = fields.One2many(
        "helpdesk.contractor.job", "ticket_id", string="Contractor Jobs")
    contractor_job_count = fields.Integer(compute="_compute_contractor_job_summary")
    contractor_id = fields.Many2one(
        "res.partner", string="Contractor", compute="_compute_contractor_job_summary",
        store=True, help="Contractor of the latest active job.")
    contractor_state = fields.Selection(
        STATES, string="Contractor Status", compute="_compute_contractor_job_summary",
        store=True)

    @api.depends("contractor_job_ids", "contractor_job_ids.state",
                 "contractor_job_ids.contractor_id")
    def _compute_contractor_job_summary(self):
        for ticket in self:
            jobs = ticket.contractor_job_ids
            active = jobs.filtered(lambda j: j.state not in ("cancelled", "declined"))
            latest = (active or jobs).sorted("id", reverse=True)[:1]
            ticket.contractor_job_count = len(jobs)
            ticket.contractor_id = latest.contractor_id
            ticket.contractor_state = latest.state or False

    def action_assign_contractor(self):
        self.ensure_one()
        return {
            "name": _("Assign Contractor"),
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.contractor.assign.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self.id},
        }

    def action_view_contractor_jobs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "helpdesk_contractor.action_helpdesk_contractor_job")
        action["domain"] = [("ticket_id", "=", self.id)]
        action["context"] = {"default_ticket_id": self.id}
        if len(self.contractor_job_ids) == 1:
            action.update(view_mode="form", res_id=self.contractor_job_ids.id,
                          views=[(False, "form")])
        return action

    # Defaults for the assign wizard ------------------------------------------------
    def _contractor_default_scope(self):
        self.ensure_one()
        parts = [self.name or ""]
        if self.description:
            parts.append(html2plaintext(self.description).strip())
        return "\n\n".join(p for p in parts if p)

    def _contractor_default_site_details(self):
        """Where to go and whom to contact. Uses the Asset Management portal
        data (location path, building, asset) when that module is installed."""
        self.ensure_one()
        lines = []
        line = self.portal_line_ids[:1] if "portal_line_ids" in self._fields else False
        if line:
            location = getattr(line, "location_display", False) or " / ".join(
                n for n in [line.location_id.name, line.sublocation_id.name] if n)
            if location:
                lines.append(_("Location: %s", location))
            if line.building:
                lines.append(_("Details: %s", line.building))
        if "asset_id" in self._fields and self.asset_id:
            lines.append(_("Equipment: %s", self.asset_id.display_name))
        if self.partner_id:
            contact = self.partner_id.display_name
            phone = self.partner_id.phone or getattr(self.partner_id, "mobile", False)
            lines.append(_("Contact: %s", f"{contact} ({phone})" if phone else contact))
        return "\n".join(lines)
