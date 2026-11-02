# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RentLegalEscalateWizard(models.TransientModel):
    _name = "rent.legal.escalate.wizard"
    _description = "Escalate a Rent Contract to a Legal Case"

    tenancy_id = fields.Many2one("tenancy.details", required=True, readonly=True)
    currency_id = fields.Many2one(related="tenancy_id.currency_id")
    amount_due = fields.Monetary(related="tenancy_id.rent_overdue_amount", string="Amount Overdue")
    overdue_since = fields.Date(related="tenancy_id.rent_overdue_since")
    notices_sent = fields.Integer(related="tenancy_id.rent_notice_count")
    direct = fields.Boolean(compute="_compute_direct")
    reason = fields.Text(string="Reason / Notes for the Lawyer", required=True)
    attachment_ids = fields.Many2many("ir.attachment", string="Documents")

    @api.depends("tenancy_id")
    def _compute_direct(self):
        _interval, max_notices = self.env["tenancy.details"]._rent_notice_settings()
        for wiz in self:
            wiz.direct = wiz.tenancy_id.rent_notice_count < max_notices

    def action_escalate(self):
        self.ensure_one()
        tenancy = self.tenancy_id
        if not tenancy.can_escalate_legal:
            raise UserError(_("This contract cannot be escalated now."))
        moves = tenancy._rent_overdue_moves()
        case = self.env["rent.legal.case"].create({
            "tenancy_id": tenancy.id,
            "direct": self.direct,
            "amount_due": sum(moves.mapped("amount_residual")),
            "overdue_since": tenancy.rent_overdue_since or (
                min(moves.mapped("invoice_date_due")) if moves else False),
            "notices_sent": tenancy.rent_notice_count,
            "invoice_refs": ", ".join(moves.mapped("name")),
            "reason": self.reason,
        })
        if self.attachment_ids:
            self.attachment_ids.write({"res_model": case._name, "res_id": case.id})
            case.attachment_ids = [(6, 0, self.attachment_ids.ids)]
        if tenancy.allow_direct_legal:
            tenancy.allow_direct_legal = False
        return {
            "type": "ir.actions.act_window",
            "res_model": "rent.legal.case",
            "res_id": case.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }


class RentLegalRejectWizard(models.TransientModel):
    _name = "rent.legal.reject.wizard"
    _description = "Reject a Rent Legal Case"

    case_id = fields.Many2one("rent.legal.case", required=True, readonly=True)
    reason = fields.Text(required=True)

    def action_reject(self):
        self.ensure_one()
        self.case_id._do_reject(self.reason)
        return {"type": "ir.actions.act_window_close"}