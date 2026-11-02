# -*- coding: utf-8 -*-
"""A contractor / vendor assignment on a helpdesk ticket.

Flow (who acts):
    draft ──(team: Send)──▶ sent ──(contractor: Accept)──▶ accepted
    sent ──(contractor: Decline)──▶ declined
    accepted / quote_rejected ──(contractor: visit + quotation)──▶ quoted
    quoted ──(team: Approve)──▶ quote_approved     (contractor told to start)
    quoted ──(team: Reject)───▶ quote_rejected     (contractor revises)
    quote_approved / work_rejected ──(contractor: work done)──▶ work_done
    work_done ──(team: Approve)──▶ work_approved   (contractor asked for invoice)
    work_done ──(team: Reject)───▶ work_rejected   (contractor redoes)
    work_approved ──(contractor: invoice)──▶ invoiced ──(team: Close)──▶ done
    any open state ──(team: Cancel)──▶ cancelled
"""
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

STATES = [
    ("draft", "Draft"),
    ("sent", "Awaiting Confirmation"),
    ("accepted", "Accepted – Site Visit"),
    ("declined", "Declined"),
    ("quoted", "Quotation Submitted"),
    ("quote_rejected", "Quotation Rejected"),
    ("quote_approved", "Work in Progress"),
    ("work_done", "Work Submitted"),
    ("work_rejected", "Work Rejected"),
    ("work_approved", "Awaiting Invoice"),
    ("invoiced", "Invoice Received"),
    ("done", "Closed"),
    ("cancelled", "Cancelled"),
]

# States in which the contractor is expected to do something on the portal.
CONTRACTOR_TURN = ("sent", "accepted", "quote_rejected", "quote_approved",
                   "work_rejected", "work_approved")
# States in which the helpdesk team is expected to do something.
TEAM_TURN = ("draft", "quoted", "work_done", "invoiced")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class HelpdeskContractorJob(models.Model):
    _name = "helpdesk.contractor.job"
    _description = "Helpdesk Contractor Job"
    _inherit = ["portal.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default=lambda self: _("New"))
    ticket_id = fields.Many2one(
        "helpdesk.ticket", string="Ticket", required=True, index=True,
        ondelete="cascade", tracking=True)
    team_id = fields.Many2one(related="ticket_id.team_id", store=True, string="Helpdesk Team")
    company_id = fields.Many2one(related="ticket_id.company_id", store=True)
    contractor_id = fields.Many2one(
        "res.partner", string="Contractor / Vendor", required=True, tracking=True,
        index=True, help="Any contact or company. The job email goes to its email address.")
    contractor_email = fields.Char(related="contractor_id.email", string="Contractor Email")
    contractor_phone = fields.Char(related="contractor_id.phone", string="Contractor Phone")
    user_id = fields.Many2one(
        "res.users", string="Responsible", tracking=True,
        default=lambda self: self.env.user,
        help="Notified (activity + message) whenever the contractor submits something.")
    state = fields.Selection(STATES, default="draft", required=True, tracking=True,
                             copy=False, index=True, string="Status")
    waiting_on = fields.Selection(
        [("team", "Helpdesk Team"), ("contractor", "Contractor"), ("none", "Nobody")],
        compute="_compute_waiting_on", store=True)

    scope = fields.Text(string="Scope / Instructions",
                        help="What the contractor is asked to do. Shown on the portal.")
    site_details = fields.Text(string="Site Details",
                               help="Address, location, contact on site. Shown on the portal.")

    # ── Contractor confirmation ──────────────────────────────────────────
    sent_date = fields.Datetime(readonly=True, copy=False)
    accepted_date = fields.Datetime(readonly=True, copy=False)
    decline_reason = fields.Text(readonly=True, copy=False)

    # ── Site visit + quotation ───────────────────────────────────────────
    visit_date = fields.Date(string="Site Visit Date", copy=False, tracking=True)
    visit_notes = fields.Text(string="Site Visit Findings", copy=False)
    visit_photo_ids = fields.Many2many(
        "ir.attachment", "hd_contractor_job_visit_photo_rel", "job_id", "attachment_id",
        string="Site Visit Photos", copy=False)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id, required=True)
    quote_amount = fields.Monetary(string="Quotation Amount", copy=False, tracking=True)
    quote_validity_days = fields.Integer(string="Quotation Valid (days)", copy=False)
    quote_duration = fields.Char(string="Estimated Duration", copy=False,
                                 help="e.g. 2 days, 1 week")
    quote_notes = fields.Text(string="Quotation Details", copy=False)
    quote_attachment_ids = fields.Many2many(
        "ir.attachment", "hd_contractor_job_quote_rel", "job_id", "attachment_id",
        string="Quotation Documents", copy=False)
    quote_date = fields.Datetime(string="Quotation Submitted On", readonly=True, copy=False)
    quote_approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    quote_approved_date = fields.Datetime(readonly=True, copy=False)
    quote_rejection_reason = fields.Text(readonly=True, copy=False)

    # ── Work completion ──────────────────────────────────────────────────
    work_start_date = fields.Date(string="Work Started", copy=False)
    work_end_date = fields.Date(string="Work Completed", copy=False)
    work_notes = fields.Text(string="Work Done", copy=False)
    work_photo_ids = fields.Many2many(
        "ir.attachment", "hd_contractor_job_work_photo_rel", "job_id", "attachment_id",
        string="Completion Photos", copy=False)
    work_submitted_date = fields.Datetime(readonly=True, copy=False)
    work_approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    work_approved_date = fields.Datetime(readonly=True, copy=False)
    work_rejection_reason = fields.Text(readonly=True, copy=False)

    # ── Invoice ──────────────────────────────────────────────────────────
    invoice_ref = fields.Char(string="Invoice Number", copy=False, tracking=True)
    invoice_date = fields.Date(string="Invoice Date", copy=False)
    invoice_amount = fields.Monetary(string="Invoice Amount", copy=False, tracking=True)
    invoice_attachment_ids = fields.Many2many(
        "ir.attachment", "hd_contractor_job_invoice_rel", "job_id", "attachment_id",
        string="Invoice Documents", copy=False)
    invoice_submitted_date = fields.Datetime(readonly=True, copy=False)
    closed_date = fields.Datetime(readonly=True, copy=False)

    # ── Helpers for views ────────────────────────────────────────────────
    portal_link = fields.Char(compute="_compute_portal_link", string="Contractor Link")

    # ─────────────────────────────────────────────────────────────────────
    # ORM
    # ─────────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "helpdesk.contractor.job") or _("New")
        jobs = super().create(vals_list)
        for job in jobs:
            partners = job.user_id.partner_id | job.ticket_id.user_id.partner_id
            if partners:
                job.message_subscribe(partner_ids=partners.ids)
            if not job.contractor_id.is_contractor:
                job.contractor_id.sudo().is_contractor = True
            job.ticket_id.message_post(
                body=_("Contractor job %(job)s created for %(contractor)s.",
                       job=job._get_html_link(), contractor=job.contractor_id.display_name),
                subtype_xmlid="mail.mt_note",
            )
        return jobs

    def _compute_access_url(self):
        super()._compute_access_url()
        for job in self:
            job.access_url = f"/contractor/job/{job.id}"

    def _compute_portal_link(self):
        for job in self:
            if job.id and job.access_token:
                job.portal_link = job.get_base_url() + job.get_portal_url()
            else:
                job.portal_link = False

    @api.depends("state")
    def _compute_waiting_on(self):
        for job in self:
            if job.state in CONTRACTOR_TURN:
                job.waiting_on = "contractor"
            elif job.state in TEAM_TURN:
                job.waiting_on = "team"
            else:
                job.waiting_on = "none"

    # ─────────────────────────────────────────────────────────────────────
    # Notifications
    # ─────────────────────────────────────────────────────────────────────
    def _send_contractor_template(self, xmlid):
        """Email the contractor using a template and log it on the job."""
        for job in self:
            if not job.contractor_id.email:
                raise UserError(_(
                    "%(contractor)s has no email address. Add one on the contact "
                    "before sending.", contractor=job.contractor_id.display_name))
            job._portal_ensure_token()
            template = self.env.ref(xmlid)
            template.send_mail(job.id, force_send=True,
                               email_layout_xmlid="mail.mail_notification_light")
            job.message_post(
                body=_("Email sent to %(contractor)s (%(email)s): %(subject)s",
                       contractor=job.contractor_id.display_name,
                       email=job.contractor_id.email,
                       subject=template.name),
                subtype_xmlid="mail.mt_note",
            )

    def _notify_team(self, summary, body):
        """Tell the helpdesk side the contractor did something: message on the
        job and the ticket (followers are emailed) + a To-Do for the responsible."""
        for job in self:
            job.message_post(body=body, subtype_xmlid="mail.mt_comment",
                             message_type="comment",
                             author_id=job.contractor_id.id)
            job.ticket_id.message_post(
                body=_("%(job)s — %(body)s", job=job._get_html_link(), body=body),
                subtype_xmlid="mail.mt_note",
            )
            responsible = job.user_id or job.ticket_id.user_id
            if responsible:
                job.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=summary,
                    note=body,
                    user_id=responsible.id,
                )

    def _close_open_activities(self):
        self.activity_ids.filtered(lambda a: a.user_id).action_feedback(
            feedback=_("Handled"))

    # ─────────────────────────────────────────────────────────────────────
    # Team actions (backend buttons)
    # ─────────────────────────────────────────────────────────────────────
    def action_send_to_contractor(self):
        for job in self:
            if job.state not in ("draft", "sent", "declined"):
                raise UserError(_("Only draft, sent or declined jobs can be (re)sent."))
            job._send_contractor_template("helpdesk_contractor.mail_template_job_request")
            job.write({"state": "sent", "sent_date": fields.Datetime.now(),
                       "decline_reason": False})
        return True

    def action_approve_quotation(self):
        for job in self:
            if job.state != "quoted":
                raise UserError(_("There is no submitted quotation to approve."))
            job.write({
                "state": "quote_approved",
                "quote_approved_by": self.env.user.id,
                "quote_approved_date": fields.Datetime.now(),
                "quote_rejection_reason": False,
            })
            job._close_open_activities()
            job._send_contractor_template("helpdesk_contractor.mail_template_quote_approved")
            job.ticket_id.message_post(
                body=_("Quotation of %(job)s approved by %(user)s (%(amount)s).",
                       job=job._get_html_link(), user=self.env.user.name,
                       amount=job._format_amount(job.quote_amount)),
                subtype_xmlid="mail.mt_note")
        return True

    def _do_reject_quotation(self, reason):
        for job in self:
            if job.state != "quoted":
                raise UserError(_("There is no submitted quotation to reject."))
            job.write({"state": "quote_rejected", "quote_rejection_reason": reason})
            job._close_open_activities()
            job._send_contractor_template("helpdesk_contractor.mail_template_quote_rejected")

    def action_approve_work(self):
        for job in self:
            if job.state != "work_done":
                raise UserError(_("There is no submitted work to approve."))
            job.write({
                "state": "work_approved",
                "work_approved_by": self.env.user.id,
                "work_approved_date": fields.Datetime.now(),
                "work_rejection_reason": False,
            })
            job._close_open_activities()
            job._send_contractor_template("helpdesk_contractor.mail_template_work_approved")
            job.ticket_id.message_post(
                body=_("Work of %(job)s approved by %(user)s.",
                       job=job._get_html_link(), user=self.env.user.name),
                subtype_xmlid="mail.mt_note")
        return True

    def _do_reject_work(self, reason):
        for job in self:
            if job.state != "work_done":
                raise UserError(_("There is no submitted work to reject."))
            job.write({"state": "work_rejected", "work_rejection_reason": reason})
            job._close_open_activities()
            job._send_contractor_template("helpdesk_contractor.mail_template_work_rejected")

    def action_open_reject_wizard(self):
        self.ensure_one()
        stage = "quotation" if self.state == "quoted" else "work"
        return {
            "name": _("Reject Quotation") if stage == "quotation" else _("Reject Work"),
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.contractor.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_job_id": self.id, "default_stage": stage},
        }

    def action_close(self):
        for job in self:
            if job.state != "invoiced":
                raise UserError(_("The job can be closed once the invoice is received."))
            job.write({"state": "done", "closed_date": fields.Datetime.now()})
            job._close_open_activities()
            job.ticket_id.message_post(
                body=_("Contractor job %(job)s closed. Invoice %(ref)s, %(amount)s.",
                       job=job._get_html_link(), ref=job.invoice_ref or "-",
                       amount=job._format_amount(job.invoice_amount)),
                subtype_xmlid="mail.mt_note")
        return True

    def action_cancel(self):
        for job in self:
            if job.state in ("done", "cancelled"):
                continue
            notify = job.state not in ("draft", "declined")
            job.write({"state": "cancelled"})
            job._close_open_activities()
            if notify and job.contractor_id.email:
                job._send_contractor_template("helpdesk_contractor.mail_template_job_cancelled")
        return True

    def action_reset_draft(self):
        self.filtered(lambda j: j.state in ("cancelled", "declined")).write({"state": "draft"})
        return True

    def action_copy_portal_link(self):
        """Show the contractor's link (e.g. to send it over WhatsApp)."""
        self.ensure_one()
        self._portal_ensure_token()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Contractor link"),
                "message": self.get_base_url() + self.get_portal_url(),
                "sticky": True,
                "type": "info",
            },
        }

    def action_preview_portal(self):
        self.ensure_one()
        self._portal_ensure_token()
        return {"type": "ir.actions.act_url", "url": self.get_portal_url(), "target": "new"}

    # ─────────────────────────────────────────────────────────────────────
    # Contractor actions (called by the portal controller, already sudo)
    # ─────────────────────────────────────────────────────────────────────
    def _portal_attach(self, files, field_name):
        """Store uploaded files and link them to the given Many2many field."""
        self.ensure_one()
        attachments = self.env["ir.attachment"]
        for upload in files or []:
            if not upload or not getattr(upload, "filename", None):
                continue
            data = upload.read()
            if not data:
                continue
            if len(data) > MAX_UPLOAD_BYTES:
                raise UserError(_("%(file)s is larger than 15 MB.", file=upload.filename))
            attachment = self.env["ir.attachment"].sudo().create({
                "name": upload.filename,
                "datas": base64.b64encode(data),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": upload.content_type or False,
            })
            attachment.generate_access_token()
            attachments |= attachment
        if attachments:
            self[field_name] = [(4, a.id) for a in attachments]
        return attachments

    def _portal_accept(self):
        self.ensure_one()
        if self.state != "sent":
            raise UserError(_("This job is no longer waiting for your confirmation."))
        self.write({"state": "accepted", "accepted_date": fields.Datetime.now()})
        self._notify_team(
            _("Contractor accepted %s", self.name),
            _("%(contractor)s accepted the job and will visit the site.",
              contractor=self.contractor_id.display_name))

    def _portal_decline(self, reason):
        self.ensure_one()
        if self.state != "sent":
            raise UserError(_("This job is no longer waiting for your confirmation."))
        self.write({"state": "declined", "decline_reason": reason or False})
        self._notify_team(
            _("Contractor declined %s", self.name),
            _("%(contractor)s declined the job. Reason: %(reason)s",
              contractor=self.contractor_id.display_name, reason=reason or "-"))

    def _portal_submit_quotation(self, values, photos, documents):
        self.ensure_one()
        if self.state not in ("accepted", "quote_rejected"):
            raise UserError(_("A quotation cannot be submitted at this stage."))
        self.write(dict(values, state="quoted", quote_date=fields.Datetime.now()))
        self._portal_attach(photos, "visit_photo_ids")
        self._portal_attach(documents, "quote_attachment_ids")
        self._notify_team(
            _("Approve quotation %s", self.name),
            _("%(contractor)s submitted a quotation of %(amount)s after the site visit. "
              "Please review and approve or reject it.",
              contractor=self.contractor_id.display_name,
              amount=self._format_amount(self.quote_amount)))

    def _portal_submit_work(self, values, photos):
        self.ensure_one()
        if self.state not in ("quote_approved", "work_rejected"):
            raise UserError(_("Work cannot be submitted at this stage."))
        self.write(dict(values, state="work_done", work_submitted_date=fields.Datetime.now()))
        self._portal_attach(photos, "work_photo_ids")
        self._notify_team(
            _("Approve completed work %s", self.name),
            _("%(contractor)s reported the work as completed. Please review the photos "
              "and details and approve or reject it.",
              contractor=self.contractor_id.display_name))

    def _portal_submit_invoice(self, values, documents):
        self.ensure_one()
        if self.state != "work_approved":
            raise UserError(_("An invoice cannot be submitted at this stage."))
        self.write(dict(values, state="invoiced", invoice_submitted_date=fields.Datetime.now()))
        self._portal_attach(documents, "invoice_attachment_ids")
        self._notify_team(
            _("Invoice received %s", self.name),
            _("%(contractor)s uploaded invoice %(ref)s for %(amount)s. Close the job "
              "once it is checked.",
              contractor=self.contractor_id.display_name, ref=self.invoice_ref or "-",
              amount=self._format_amount(self.invoice_amount)))

    # ─────────────────────────────────────────────────────────────────────
    # Misc
    # ─────────────────────────────────────────────────────────────────────
    def _format_amount(self, amount):
        self.ensure_one()
        return format_amount(self.env, amount or 0.0, self.currency_id)

    def _attachment_url(self, attachment):
        """Public URL of an attachment for the portal page."""
        if not attachment.access_token:
            attachment.sudo().generate_access_token()
        return f"/web/content/{attachment.id}?access_token={attachment.access_token}"

    def _get_report_base_filename(self):
        self.ensure_one()
        return self.name
