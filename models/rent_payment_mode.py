# -*- coding: utf-8 -*-
"""Rent contract payment method: Standard Order (invoices only) or
Cheque Payment (invoices + PDC cheques), plus payment tracking for the
Standard Order dashboard."""
from odoo import api, fields, models

PAYMENT_MODES = [
    ("standard", "Standard Order"),
    ("cheque", "Cheque Payment"),
]

PAY_STATUS = [
    ("draft", "Draft Invoice"),
    ("unpaid", "Payment Due"),
    ("partial", "Partially Paid"),
    ("paid", "Paid"),
    ("cancel", "Cancelled"),
]


class TenancyDetails(models.Model):
    _inherit = "tenancy.details"

    payment_mode = fields.Selection(
        PAYMENT_MODES, string="Payment Method",
        compute="_compute_payment_mode", inverse="_inverse_payment_mode",
        store=True, readonly=False, tracking=True,
        help="Standard Order: only the invoices are created.\n"
             "Cheque Payment: the invoices are created together with the PDC cheques.")

    pay_amount_invoiced = fields.Monetary(compute="_compute_pay_summary", store=True,
                                          string="Invoiced")
    pay_amount_paid = fields.Monetary(compute="_compute_pay_summary", store=True, string="Paid")
    pay_amount_due = fields.Monetary(compute="_compute_pay_summary", store=True, string="Balance Due")
    pay_next_due_date = fields.Date(compute="_compute_pay_summary", store=True,
                                    string="Next Due Date")
    pay_status = fields.Selection(
        [("no_invoice", "No Invoice"), ("due", "Payment Due"),
         ("partial", "Partially Paid"), ("paid", "Fully Paid")],
        compute="_compute_pay_summary", store=True, string="Payment Status")
    pay_overdue_amount = fields.Monetary(compute="_compute_pay_overdue", string="Overdue Amount")
    pay_overdue_count = fields.Integer(compute="_compute_pay_overdue", string="Overdue Invoices")

    # ── payment method <-> PDC flag ─────────────────────────────────────────
    @api.depends("pdc_enabled")
    def _compute_payment_mode(self):
        for rec in self:
            rec.payment_mode = "cheque" if rec.pdc_enabled else "standard"

    def _inverse_payment_mode(self):
        for rec in self:
            enabled = rec.payment_mode == "cheque"
            if rec.pdc_enabled != enabled:
                rec.pdc_enabled = enabled

    @api.onchange("payment_mode")
    def _onchange_payment_mode(self):
        self.pdc_enabled = self.payment_mode == "cheque"
        if self.payment_mode == "cheque" and not self.pdc_auto_generate:
            self.pdc_auto_generate = True

    # ── payment summary ─────────────────────────────────────────────────────
    @api.depends("rent_invoice_ids.pay_status", "rent_invoice_ids.pay_amount_total",
                 "rent_invoice_ids.pay_amount_residual", "rent_invoice_ids.pay_due_date")
    def _compute_pay_summary(self):
        for rec in self:
            lines = rec.rent_invoice_ids.filtered(lambda l: l.pay_status in ("unpaid", "partial", "paid"))
            invoiced = sum(lines.mapped("pay_amount_total"))
            due = sum(lines.mapped("pay_amount_residual"))
            open_lines = lines.filtered(lambda l: l.pay_status in ("unpaid", "partial"))
            rec.pay_amount_invoiced = invoiced
            rec.pay_amount_due = due
            rec.pay_amount_paid = invoiced - due
            rec.pay_next_due_date = min(
                open_lines.filtered("pay_due_date").mapped("pay_due_date"), default=False)
            if not lines:
                rec.pay_status = "no_invoice"
            elif not open_lines:
                rec.pay_status = "paid"
            elif rec.pay_amount_paid > 0:
                rec.pay_status = "partial"
            else:
                rec.pay_status = "due"

    def _compute_pay_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            overdue = rec.rent_invoice_ids.filtered(
                lambda l: l.pay_status in ("unpaid", "partial") and l.pay_due_date and l.pay_due_date < today)
            rec.pay_overdue_count = len(overdue)
            rec.pay_overdue_amount = sum(overdue.mapped("pay_amount_residual"))

    def action_view_payment_invoices(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "helpdesk_contractor.action_standard_order_payments")
        action["domain"] = [("tenancy_id", "=", self.id), ("rent_invoice_id", "!=", False)]
        action["context"] = {"search_default_hide_cancel": 1}
        action["name"] = self.display_name
        return action


class RentInvoice(models.Model):
    _inherit = "rent.invoice"

    payment_mode = fields.Selection(related="tenancy_id.payment_mode", store=True,
                                    string="Payment Method")
    pay_property_id = fields.Many2one(related="tenancy_id.property_id", store=True, string="Property")
    pay_due_date = fields.Date(related="rent_invoice_id.invoice_date_due", store=True,
                               string="Due Date")
    pay_amount_total = fields.Monetary(related="rent_invoice_id.amount_total", store=True,
                                       string="Invoice Total")
    pay_amount_residual = fields.Monetary(related="rent_invoice_id.amount_residual", store=True,
                                          string="Amount Due")
    pay_amount_paid = fields.Monetary(compute="_compute_pay_status", store=True, string="Amount Paid")
    pay_status = fields.Selection(PAY_STATUS, compute="_compute_pay_status", store=True,
                                  string="Payment Status", group_expand="_group_expand_pay_status")
    pay_is_overdue = fields.Boolean(compute="_compute_pay_overdue", string="Overdue")
    pay_days_overdue = fields.Integer(compute="_compute_pay_overdue", string="Days Overdue")

    @api.depends("rent_invoice_id.state", "rent_invoice_id.payment_state",
                 "rent_invoice_id.amount_total", "rent_invoice_id.amount_residual")
    def _compute_pay_status(self):
        for rec in self:
            move = rec.rent_invoice_id
            rec.pay_amount_paid = (move.amount_total - move.amount_residual) if move else 0.0
            if not move:
                rec.pay_status = False
            elif move.state == "cancel" or move.payment_state == "reversed":
                rec.pay_status = "cancel"
            elif move.state == "draft":
                rec.pay_status = "draft"
            elif move.payment_state in ("paid", "in_payment"):
                rec.pay_status = "paid"
            elif move.payment_state == "partial":
                rec.pay_status = "partial"
            else:
                rec.pay_status = "unpaid"

    def _compute_pay_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            late = rec.pay_status in ("unpaid", "partial") and rec.pay_due_date and rec.pay_due_date < today
            rec.pay_is_overdue = bool(late)
            rec.pay_days_overdue = (today - rec.pay_due_date).days if late else 0

    @api.model
    def _group_expand_pay_status(self, states, domain):
        return ["draft", "unpaid", "partial", "paid"]

    def action_open_invoice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.rent_invoice_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }

    def action_register_payment(self):
        self.ensure_one()
        return self.rent_invoice_id.action_register_payment()