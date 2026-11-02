# -*- coding: utf-8 -*-
"""PDC cheque dashboard and Cheque Register report (sh_pdc)."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PDC_STATES = [
    ("draft", "Draft"), ("registered", "Registered"), ("returned", "Returned"),
    ("deposited", "Deposited"), ("bounced", "Bounced"), ("done", "Done"),
    ("cancel", "Cancelled"),
]


class PdcWizard(models.Model):
    _inherit = "pdc.wizard"

    deposit_date = fields.Date(compute="_compute_deposit_info", store=True, string="Deposit Date",
                               help="Date of the deposit journal entry.")
    deposited_by_id = fields.Many2one("res.users", compute="_compute_deposit_info", store=True,
                                      string="Deposited By")
    pdc_property_id = fields.Many2one(related="tenancy_id.property_id", store=True, string="Property")
    pdc_to_deposit = fields.Boolean(compute="_compute_pdc_flags", string="Ready to Deposit")
    pdc_late_deposit = fields.Boolean(compute="_compute_pdc_flags", string="Past Due, Not Deposited")
    pdc_days_to_due = fields.Integer(compute="_compute_pdc_flags", string="Days to Due")

    @api.depends("deposited_debit", "deposited_debit.date", "deposited_debit.move_id.create_uid")
    def _compute_deposit_info(self):
        for rec in self:
            line = rec.deposited_debit
            rec.deposit_date = line.date if line else False
            rec.deposited_by_id = line.move_id.create_uid if line else False

    def _compute_pdc_flags(self):
        today = fields.Date.context_today(self)
        for rec in self:
            waiting = rec.state in ("draft", "registered")
            rec.pdc_days_to_due = (rec.due_date - today).days if rec.due_date else 0
            rec.pdc_to_deposit = bool(waiting and rec.due_date and rec.due_date == today)
            rec.pdc_late_deposit = bool(waiting and rec.due_date and rec.due_date < today)

    def action_print_pdc_register(self):
        """Print the selected cheques in the Cheque Register layout."""
        if not self:
            raise UserError(_("Select at least one cheque."))
        return self.env.ref("helpdesk_contractor.action_report_pdc_register").report_action(
            self, data={"ids": self.ids, "title": _("Selected Cheques")})


class PdcRegisterWizard(models.TransientModel):
    _name = "pdc.register.report.wizard"
    _description = "PDC Cheque Register Report"

    date_basis = fields.Selection([
        ("deposit_date", "Deposit Date"),
        ("due_date", "Cheque Due Date"),
        ("payment_date", "Payment Date"),
    ], default="deposit_date", required=True, string="Filter Dates By")
    date_from = fields.Date(string="From", default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string="To", default=fields.Date.context_today)
    status_scope = fields.Selection([
        ("deposited", "Deposited & Cleared (Deposited, Done)"),
        ("deposited_only", "Deposited, Not Yet Cleared"),
        ("pending", "Not Yet Deposited (Draft, Registered)"),
        ("bounced", "Bounced / Returned"),
        ("all", "All (except Cancelled)"),
    ], default="deposited", required=True, string="Cheques")
    payment_type = fields.Selection([
        ("receive_money", "Received (Customers / Tenants)"),
        ("send_money", "Issued (Vendors)"),
    ], string="Direction", help="Leave empty for both.")
    partner_id = fields.Many2one("res.partner", string="Partner / Tenant")
    bank_id = fields.Many2one("res.bank", string="Bank")
    journal_id = fields.Many2one("account.journal", string="Deposit Journal", domain="[('type', '=', 'bank')]")
    tenancy_id = fields.Many2one("tenancy.details", string="Rent Contract")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, string="Company")

    _SCOPES = {
        "deposited": ["deposited", "done"],
        "deposited_only": ["deposited"],
        "pending": ["draft", "registered"],
        "bounced": ["bounced", "returned"],
        "all": ["draft", "registered", "returned", "deposited", "bounced", "done"],
    }

    def _get_domain(self):
        self.ensure_one()
        domain = [("state", "in", self._SCOPES[self.status_scope])]
        if self.date_from:
            domain.append((self.date_basis, ">=", self.date_from))
        if self.date_to:
            domain.append((self.date_basis, "<=", self.date_to))
        for fname in ("payment_type", "partner_id", "bank_id", "journal_id", "tenancy_id", "company_id"):
            value = self[fname]
            if value:
                domain.append((fname, "=", value.id if hasattr(value, "id") else value))
        return domain

    def _get_title(self):
        self.ensure_one()
        return dict(self._fields["status_scope"].selection)[self.status_scope]

    def _get_filters_text(self):
        self.ensure_one()
        parts = []
        basis = dict(self._fields["date_basis"].selection)[self.date_basis]
        if self.date_from or self.date_to:
            parts.append(_("%(basis)s: %(start)s to %(end)s", basis=basis,
                           start=fields.Date.to_string(self.date_from) if self.date_from else "...",
                           end=fields.Date.to_string(self.date_to) if self.date_to else "..."))
        if self.payment_type:
            parts.append(dict(self._fields["payment_type"].selection)[self.payment_type])
        for fname in ("partner_id", "bank_id", "journal_id", "tenancy_id"):
            if self[fname]:
                parts.append(f"{self._fields[fname].string}: {self[fname].display_name}")
        return " · ".join(parts)

    def action_view(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "helpdesk_contractor.action_pdc_dashboard_list")
        action["domain"] = self._get_domain()
        action["context"] = {}
        action["name"] = self._get_title()
        return action

    def action_print(self):
        self.ensure_one()
        cheques = self.env["pdc.wizard"].search(self._get_domain(), order=f"{self.date_basis}, id")
        if not cheques:
            raise UserError(_("No cheque matches these filters."))
        return self.env.ref("helpdesk_contractor.action_report_pdc_register").report_action(
            cheques, data={"ids": cheques.ids, "title": self._get_title(),
                           "filters": self._get_filters_text()})


class ReportPdcRegister(models.AbstractModel):
    _name = "report.helpdesk_contractor.report_pdc_register"
    _description = "PDC Cheque Register"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        ids = data.get("ids") or docids or []
        docs = self.env["pdc.wizard"].browse(ids).exists()
        currency = docs[:1].currency_id or self.env.company.currency_id
        states = dict(PDC_STATES)
        by_state, by_bank = {}, {}
        for chq in docs:
            label = states.get(chq.state, chq.state)
            by_state.setdefault(label, [0, 0.0])
            by_state[label][0] += 1
            by_state[label][1] += chq.payment_amount
            bank = chq.bank_id.name or _("Not specified")
            by_bank.setdefault(bank, [0, 0.0])
            by_bank[bank][0] += 1
            by_bank[bank][1] += chq.payment_amount
        return {
            "doc_ids": docs.ids,
            "doc_model": "pdc.wizard",
            "docs": docs,
            "title": data.get("title") or _("Cheque Register"),
            "filters": data.get("filters") or "",
            "currency": currency,
            "total": sum(docs.mapped("payment_amount")),
            "by_state": sorted(by_state.items()),
            "by_bank": sorted(by_bank.items()),
            "company": self.env.company,
        }