# -*- coding: utf-8 -*-
"""Overdue rent follow-up and legal escalation for rental_management.

* A daily job looks at every running tenancy. When one of its invoices is
  overdue, an internal notice is sent every 14 days counted from the first
  overdue due date (day 14, 28, 42, 56), 4 notices at most. Paying everything
  resets the counter.
* After the 4th notice, or straight away when "Escalate Directly" is ticked,
  the contract shows "Escalate to Legal Case". This opens a legal case that
  waits for the internal lawyer's approval (Legal Cases > To Approve).
* On approval the property's status becomes "Under Legal Case". Closing the
  case puts it back to On Rent (running contract) or Available.
"""
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

NOTICE_INTERVAL_DAYS = 14
MAX_NOTICES = 4


# Property statuses of rental_management when its stage field is a fixed list.
BASE_PROPERTY_STAGES = [
    ("draft", "Draft"),
    ("available", "Available"),
    ("booked", "In Booking"),
    ("on_lease", "On Rent"),
    ("sale", "In Sale"),
    ("sold", "Sold"),
]
LEGAL_STAGE = ("under_legal", "Under Legal Case")


class PropertyDetails(models.Model):
    _inherit = "property.details"

    # rental_management defines `stage` either as a fixed list or through a
    # `_selection_stage` method depending on its version; selection_add only
    # works with the first. Use our own method, which extends whichever exists.
    stage = fields.Selection(selection="_selection_stage_with_legal")
    legal_case_ids = fields.One2many("rent.legal.case", "property_id", string="Legal Cases")

    @api.model
    def _selection_stage_with_legal(self):
        parent = getattr(super(), "_selection_stage", None)
        stages = list(parent()) if callable(parent) else list(BASE_PROPERTY_STAGES)
        if LEGAL_STAGE[0] not in dict(stages):
            stages.append(LEGAL_STAGE)
        return stages

    def _expand_groups(self, states, domain):
        """Kanban: show the Under Legal Case column too."""
        parent = getattr(super(), "_expand_groups", None)
        groups = list(parent(states, domain)) if callable(parent) else [
            key for key, _label in self._selection_stage_with_legal()]
        if LEGAL_STAGE[0] not in groups:
            groups.append(LEGAL_STAGE[0])
        return groups


class TenancyOverdueNotice(models.Model):
    _name = "tenancy.overdue.notice"
    _description = "Overdue Rent Internal Notice"
    _order = "notice_date desc, id desc"

    tenancy_id = fields.Many2one("tenancy.details", required=True, ondelete="cascade", index=True)
    notice_no = fields.Integer(string="Notice", required=True)
    notice_date = fields.Date(required=True, default=fields.Date.context_today)
    overdue_since = fields.Date(string="Overdue Since")
    days_overdue = fields.Integer()
    currency_id = fields.Many2one(related="tenancy_id.currency_id")
    amount_due = fields.Monetary(string="Amount Overdue")
    invoice_refs = fields.Char(string="Invoices")
    notified_user_id = fields.Many2one("res.users", string="Notified")


class TenancyDetails(models.Model):
    _inherit = "tenancy.details"

    rent_overdue_since = fields.Date(
        string="Overdue Since", readonly=True, copy=False, tracking=True,
        help="Due date of the oldest unpaid invoice. Notices are counted from this date.")
    rent_notice_count = fields.Integer(
        string="Notices Sent", readonly=True, copy=False, tracking=True)
    rent_last_notice_date = fields.Date(string="Last Notice", readonly=True, copy=False)
    rent_next_notice_date = fields.Date(
        string="Next Notice", compute="_compute_rent_overdue", store=False)
    rent_overdue_amount = fields.Monetary(
        string="Amount Overdue", compute="_compute_rent_overdue", store=False)
    rent_overdue_invoice_count = fields.Integer(
        string="Overdue Invoices", compute="_compute_rent_overdue", store=False)
    rent_notice_ids = fields.One2many("tenancy.overdue.notice", "tenancy_id", string="Notices")

    allow_direct_legal = fields.Boolean(
        string="Escalate Directly", tracking=True, copy=False,
        help="Show 'Escalate to Legal Case' now, without waiting for the 4 overdue notices.")
    legal_case_ids = fields.One2many("rent.legal.case", "tenancy_id", string="Legal Cases")
    legal_case_count = fields.Integer(compute="_compute_can_escalate_legal")
    legal_state = fields.Selection(
        [("none", "None"), ("pending", "Awaiting Lawyer Approval"),
         ("approved", "Under Legal Case"), ("rejected", "Rejected"), ("closed", "Closed")],
        string="Legal Status", compute="_compute_legal_state", store=True)
    can_escalate_legal = fields.Boolean(compute="_compute_can_escalate_legal")

    # ── computes ────────────────────────────────────────────────────────────
    def _rent_overdue_moves(self, today=None):
        """Posted customer invoices of this contract that are past due and unpaid."""
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        return self.rent_invoice_ids.mapped("rent_invoice_id").filtered(
            lambda m: m.state == "posted"
            and m.payment_state in ("not_paid", "partial")
            and m.invoice_date_due
            and m.invoice_date_due < today
        )

    @api.depends("rent_invoice_ids.rent_invoice_id.payment_state",
                 "rent_overdue_since", "rent_notice_count")
    def _compute_rent_overdue(self):
        interval, max_notices = self._rent_notice_settings()
        for rec in self:
            moves = rec._rent_overdue_moves() if rec.id else self.env["account.move"]
            rec.rent_overdue_amount = sum(moves.mapped("amount_residual"))
            rec.rent_overdue_invoice_count = len(moves)
            if rec.rent_overdue_since and rec.rent_notice_count < max_notices:
                rec.rent_next_notice_date = rec._rent_next_notice_on(rec.rent_overdue_since, interval)
            else:
                rec.rent_next_notice_date = False

    @api.depends("legal_case_ids.state")
    def _compute_legal_state(self):
        for rec in self:
            latest = rec.legal_case_ids.sorted("id", reverse=True)[:1]
            rec.legal_state = latest.state if latest else "none"

    @api.depends("legal_case_ids", "legal_state", "rent_notice_count", "allow_direct_legal")
    def _compute_can_escalate_legal(self):
        _interval, max_notices = self._rent_notice_settings()
        for rec in self:
            rec.legal_case_count = len(rec.legal_case_ids)
            rec.can_escalate_legal = (
                rec.legal_state not in ("pending", "approved")
                and (rec.allow_direct_legal or rec.rent_notice_count >= max_notices)
            )

    def _rent_next_notice_on(self, since, interval):
        """Notice n is due `interval` x n days after the first overdue date, and
        never sooner than `interval` days after the previous notice (so a
        contract that is already long overdue is not sent 4 notices in 4 days)."""
        self.ensure_one()
        due = since + timedelta(days=interval * (self.rent_notice_count + 1))
        if self.rent_last_notice_date:
            due = max(due, self.rent_last_notice_date + timedelta(days=interval))
        return due

    @api.model
    def _rent_notice_settings(self):
        params = self.env["ir.config_parameter"].sudo()
        try:
            interval = int(params.get_param("helpdesk_contractor.rent_notice_interval_days",
                                            NOTICE_INTERVAL_DAYS))
            max_notices = int(params.get_param("helpdesk_contractor.rent_notice_max",
                                               MAX_NOTICES))
        except (TypeError, ValueError):
            interval, max_notices = NOTICE_INTERVAL_DAYS, MAX_NOTICES
        return max(interval, 1), max(max_notices, 1)

    # ── daily job ───────────────────────────────────────────────────────────
    @api.model
    def _cron_rent_overdue_notices(self):
        interval, max_notices = self._rent_notice_settings()
        today = fields.Date.context_today(self)
        tenancies = self.search([("contract_type", "in", ("running_contract", "expire_contract"))])
        for tenancy in tenancies:
            moves = tenancy._rent_overdue_moves(today)
            if not moves:
                if tenancy.rent_overdue_since or tenancy.rent_notice_count:
                    tenancy.write({"rent_overdue_since": False, "rent_notice_count": 0})
                    tenancy.message_post(
                        body=_("All overdue invoices are paid. Overdue notices reset."),
                        subtype_xmlid="mail.mt_note")
                continue
            since = min(moves.mapped("invoice_date_due"))
            if tenancy.rent_overdue_since != since:
                tenancy.rent_overdue_since = since
            if tenancy.rent_notice_count >= max_notices:
                continue
            if today >= tenancy._rent_next_notice_on(since, interval):
                tenancy._send_rent_overdue_notice(moves, since, today, max_notices)

    def _send_rent_overdue_notice(self, moves, since, today, max_notices):
        self.ensure_one()
        number = self.rent_notice_count + 1
        amount = sum(moves.mapped("amount_residual"))
        refs = ", ".join(moves.mapped("name"))
        days = (today - since).days
        responsible = self.responsible_id
        self.env["tenancy.overdue.notice"].create({
            "tenancy_id": self.id,
            "notice_no": number,
            "notice_date": today,
            "overdue_since": since,
            "days_overdue": days,
            "amount_due": amount,
            "invoice_refs": refs,
            "notified_user_id": responsible.id or False,
        })
        self.write({"rent_notice_count": number, "rent_last_notice_date": today})
        final = number >= max_notices
        body = Markup(_(
            "<b>Overdue rent — internal notice %(no)s of %(max)s.</b><br/>"
            "Tenant: %(tenant)s · Property: %(property)s<br/>"
            "Overdue since %(since)s (%(days)s days): <b>%(amount)s</b> on %(refs)s."
        )) % {
            "no": number, "max": max_notices,
            "tenant": self.tenancy_id.display_name or "-",
            "property": self.property_id.display_name or "-",
            "since": fields.Date.to_string(since), "days": days,
            "amount": self._rent_format_amount(amount), "refs": refs,
        }
        if final:
            body += Markup(_("<br/><b>This was the last notice.</b> The contract can now be "
                             "escalated to a legal case."))
        partner_ids = responsible.partner_id.ids if responsible else []
        self.message_post(body=body, subtype_xmlid="mail.mt_comment",
                          message_type="comment", partner_ids=partner_ids)
        if responsible:
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Overdue rent: notice %(no)s of %(max)s", no=number, max=max_notices)
                        + (_(" — escalate to legal?") if final else ""),
                note=body,
                user_id=responsible.id,
            )

    def _rent_format_amount(self, amount):
        self.ensure_one()
        return format_amount(self.env, amount or 0.0, self.currency_id or self.env.company.currency_id)

    # ── buttons ─────────────────────────────────────────────────────────────
    def action_escalate_legal(self):
        self.ensure_one()
        if not self.can_escalate_legal:
            raise UserError(_("This contract cannot be escalated yet: send the 4 overdue "
                              "notices first, or tick 'Escalate Directly'."))
        return {
            "name": _("Escalate to Legal Case"),
            "type": "ir.actions.act_window",
            "res_model": "rent.legal.escalate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_tenancy_id": self.id},
        }

    def action_view_legal_cases(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "helpdesk_contractor.action_rent_legal_case")
        action["domain"] = [("tenancy_id", "=", self.id)]
        action["context"] = {"create": False}
        if len(self.legal_case_ids) == 1:
            action.update(view_mode="form", res_id=self.legal_case_ids.id, views=[(False, "form")])
        return action

    def action_send_overdue_notice_now(self):
        """Manual: send the next notice today (e.g. after a meeting with the tenant)."""
        _interval, max_notices = self._rent_notice_settings()
        today = fields.Date.context_today(self)
        for rec in self:
            moves = rec._rent_overdue_moves(today)
            if not moves:
                raise UserError(_("%s has no overdue invoice.", rec.display_name))
            if rec.rent_notice_count >= max_notices:
                raise UserError(_("All %s notices have already been sent.", max_notices))
            since = min(moves.mapped("invoice_date_due"))
            rec.rent_overdue_since = rec.rent_overdue_since or since
            rec._send_rent_overdue_notice(moves, rec.rent_overdue_since, today, max_notices)
        return True


class RentLegalCase(models.Model):
    _name = "rent.legal.case"
    _description = "Rent Legal Case"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default=lambda self: _("New"))
    tenancy_id = fields.Many2one("tenancy.details", string="Contract", required=True,
                                 readonly=True, ondelete="restrict", index=True)
    property_id = fields.Many2one(related="tenancy_id.property_id", store=True, string="Property")
    tenant_id = fields.Many2one(related="tenancy_id.tenancy_id", store=True, string="Tenant")
    company_id = fields.Many2one(related="tenancy_id.company_id", store=True)
    currency_id = fields.Many2one(related="tenancy_id.currency_id", store=True)
    state = fields.Selection(
        [("pending", "Awaiting Lawyer Approval"), ("approved", "Under Legal Case"),
         ("rejected", "Rejected"), ("closed", "Closed")],
        default="pending", required=True, tracking=True, index=True, string="Status")
    direct = fields.Boolean(string="Escalated Directly", readonly=True,
                            help="Escalated before the 4 overdue notices were sent.")
    amount_due = fields.Monetary(string="Amount Overdue", readonly=True)
    overdue_since = fields.Date(readonly=True)
    notices_sent = fields.Integer(readonly=True)
    invoice_refs = fields.Char(string="Overdue Invoices", readonly=True)
    reason = fields.Text(string="Reason / Notes", tracking=True)
    requested_by = fields.Many2one("res.users", readonly=True, default=lambda self: self.env.user)
    request_date = fields.Datetime(readonly=True, default=fields.Datetime.now)
    lawyer_id = fields.Many2one("res.users", string="Approved / Rejected By", readonly=True)
    decision_date = fields.Datetime(readonly=True)
    rejection_reason = fields.Text(readonly=True)
    case_reference = fields.Char(string="Court / Case Reference", tracking=True)
    lawyer_notes = fields.Text(string="Lawyer Notes")
    attachment_ids = fields.Many2many("ir.attachment", "rent_legal_case_attachment_rel",
                                      "case_id", "attachment_id", string="Documents")
    closed_date = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("rent.legal.case") or _("New")
        cases = super().create(vals_list)
        for case in cases:
            case._notify_lawyers()
        return cases

    def _lawyer_users(self):
        """Who approves legal cases: the Rental Managers."""
        group = self.env.ref("rental_management.property_rental_manager", raise_if_not_found=False)
        return group.user_ids.filtered(lambda u: u.active and not u.share) if group \
            else self.env["res.users"]

    def _notify_lawyers(self):
        for case in self:
            lawyers = case._lawyer_users()
            body = _("Legal case %(case)s for %(tenant)s (%(property)s), %(amount)s overdue, "
                     "needs your approval.",
                     case=case.name, tenant=case.tenant_id.display_name or "-",
                     property=case.property_id.display_name or "-",
                     amount=case.tenancy_id._rent_format_amount(case.amount_due))
            if lawyers:
                case.message_subscribe(partner_ids=lawyers.partner_id.ids)
                case.message_post(body=body, subtype_xmlid="mail.mt_comment",
                                  message_type="comment", partner_ids=lawyers.partner_id.ids)
                for lawyer in lawyers:
                    case.activity_schedule("mail.mail_activity_data_todo",
                                           summary=_("Approve legal case %s", case.name),
                                           note=body, user_id=lawyer.id)
            case.tenancy_id.message_post(
                body=_("Escalated to legal case %s — awaiting lawyer approval.",
                       case._get_html_link()),
                subtype_xmlid="mail.mt_note")

    def _check_lawyer(self):
        if not self.env.user.has_group("rental_management.property_rental_manager"):
            raise UserError(_("Only a Rental Manager can approve, reject or close legal cases."))

    def action_approve(self):
        self._check_lawyer()
        for case in self:
            if case.state != "pending":
                raise UserError(_("%s is not waiting for approval.", case.name))
            case.write({"state": "approved", "lawyer_id": self.env.user.id,
                        "decision_date": fields.Datetime.now()})
            if case.property_id:
                case.property_id.sudo().stage = "under_legal"
            case.activity_ids.action_feedback(feedback=_("Approved"))
            case.tenancy_id.message_post(
                body=_("Legal case %(case)s approved by %(user)s. Property set to Under Legal Case.",
                       case=case._get_html_link(), user=self.env.user.name),
                subtype_xmlid="mail.mt_comment", message_type="comment",
                partner_ids=case.requested_by.partner_id.ids)
        return True

    def action_open_reject_wizard(self):
        self._check_lawyer()
        self.ensure_one()
        return {
            "name": _("Reject Legal Case"),
            "type": "ir.actions.act_window",
            "res_model": "rent.legal.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_case_id": self.id},
        }

    def _do_reject(self, reason):
        self._check_lawyer()
        for case in self:
            if case.state != "pending":
                raise UserError(_("%s is not waiting for approval.", case.name))
            case.write({"state": "rejected", "lawyer_id": self.env.user.id,
                        "decision_date": fields.Datetime.now(), "rejection_reason": reason})
            case.activity_ids.action_feedback(feedback=_("Rejected"))
            case.tenancy_id.message_post(
                body=_("Legal case %(case)s rejected by %(user)s: %(reason)s",
                       case=case._get_html_link(), user=self.env.user.name, reason=reason),
                subtype_xmlid="mail.mt_comment", message_type="comment",
                partner_ids=case.requested_by.partner_id.ids)

    def action_close(self):
        self._check_lawyer()
        for case in self:
            if case.state != "approved":
                raise UserError(_("Only an approved case can be closed."))
            case.write({"state": "closed", "closed_date": fields.Datetime.now()})
            prop = case.property_id.sudo()
            if prop and prop.stage == "under_legal":
                running = case.tenancy_id.contract_type == "running_contract"
                prop.stage = "on_lease" if running else "available"
            case.tenancy_id.message_post(
                body=_("Legal case %s closed.", case._get_html_link()),
                subtype_xmlid="mail.mt_note")
        return True