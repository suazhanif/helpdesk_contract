# -*- coding: utf-8 -*-
"""Contractor portal: one page per job, opened from the emailed link.

The access token in the link is the contractor's key, so no Odoo login is
needed. Every POST re-checks the token and the job's current state.
"""
import logging
from urllib.parse import urlencode

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)

STEPS = [
    ("confirm", "Confirm"),
    ("quotation", "Site visit & quotation"),
    ("work", "Work"),
    ("invoice", "Invoice"),
    ("closed", "Closed"),
]
STATE_STEP = {
    "draft": "confirm", "sent": "confirm", "declined": "confirm",
    "accepted": "quotation", "quoted": "quotation", "quote_rejected": "quotation",
    "quote_approved": "work", "work_done": "work", "work_rejected": "work",
    "work_approved": "invoice", "invoiced": "invoice",
    "done": "closed", "cancelled": "closed",
}


class ContractorPortal(CustomerPortal):

    # ── helpers ─────────────────────────────────────────────────────────────
    def _contractor_job(self, job_id, access_token):
        """Sudo job if the token matches, else None."""
        try:
            return self._document_check_access("helpdesk.contractor.job", job_id, access_token)
        except (AccessError, MissingError):
            return None

    @staticmethod
    def _float(value):
        try:
            return float(str(value or "0").replace(",", "").strip() or 0)
        except ValueError:
            raise UserError(_("Please enter a valid amount."))

    @staticmethod
    def _date(value):
        return fields.Date.to_date(value) if value else False

    @staticmethod
    def _text(value):
        return (value or "").strip() or False

    def _files(self, name):
        return [f for f in request.httprequest.files.getlist(name) if f and f.filename]

    def _redirect(self, job, message=None):
        query = {"access_token": job.access_token}
        if message:
            query["message"] = message
        return request.redirect(f"/contractor/job/{job.id}?{urlencode(query)}")

    def _render_job(self, job, error=None, message=None):
        current = STATE_STEP.get(job.state, "confirm")
        index = [s[0] for s in STEPS].index(current)
        return request.render("helpdesk_contractor.portal_contractor_job_page", {
            "job": job,
            "steps": STEPS,
            "current_step": index,
            "state_label": dict(job._fields["state"].selection).get(job.state),
            "error": error,
            "message": message,
            "attachment_url": job._attachment_url,
            "access_token": job.access_token,
            "page_name": "contractor_job",
        })

    def _handle(self, job_id, access_token, action, success_message):
        job = self._contractor_job(job_id, access_token)
        if not job:
            return request.not_found()
        try:
            # All or nothing: a failed upload must not leave the job half-updated.
            with request.env.cr.savepoint():
                action(job)
        except UserError as error:
            request.env.invalidate_all()
            return self._render_job(job, error=str(error.args[0] if error.args else error))
        return self._redirect(job, success_message)

    # ── page ────────────────────────────────────────────────────────────────
    @http.route("/contractor/job/<int:job_id>", type="http", auth="public", website=True)
    def contractor_job(self, job_id, access_token=None, message=None, **kw):
        job = self._contractor_job(job_id, access_token)
        if not job:
            return request.not_found()
        return self._render_job(job, message=message)

    # ── contractor actions ──────────────────────────────────────────────────
    @http.route("/contractor/job/<int:job_id>/accept", type="http", auth="public",
                website=True, methods=["POST"])
    def contractor_accept(self, job_id, access_token=None, **kw):
        return self._handle(job_id, access_token, lambda job: job._portal_accept(),
                            _("Thank you. The job is accepted — please visit the site and "
                              "submit your quotation below."))

    @http.route("/contractor/job/<int:job_id>/decline", type="http", auth="public",
                website=True, methods=["POST"])
    def contractor_decline(self, job_id, access_token=None, reason=None, **kw):
        return self._handle(job_id, access_token,
                            lambda job: job._portal_decline(self._text(reason)),
                            _("You have declined this job. Thank you for letting us know."))

    @http.route("/contractor/job/<int:job_id>/quotation", type="http", auth="public",
                website=True, methods=["POST"])
    def contractor_quotation(self, job_id, access_token=None, **post):
        def action(job):
            amount = self._float(post.get("quote_amount"))
            if amount <= 0:
                raise UserError(_("Please enter the quotation amount."))
            photos, documents = self._files("visit_photos"), self._files("quote_documents")
            if not photos and not job.visit_photo_ids:
                raise UserError(_("Please upload at least one site visit photo."))
            job._portal_submit_quotation({
                "visit_date": self._date(post.get("visit_date")) or fields.Date.today(),
                "visit_notes": self._text(post.get("visit_notes")),
                "quote_amount": amount,
                "quote_duration": self._text(post.get("quote_duration")),
                "quote_validity_days": int(post.get("quote_validity_days") or 0),
                "quote_notes": self._text(post.get("quote_notes")),
            }, photos, documents)
        return self._handle(job_id, access_token, action,
                            _("Your quotation has been submitted. We will notify you once "
                              "it is reviewed."))

    @http.route("/contractor/job/<int:job_id>/work", type="http", auth="public",
                website=True, methods=["POST"])
    def contractor_work(self, job_id, access_token=None, **post):
        def action(job):
            notes = self._text(post.get("work_notes"))
            if not notes:
                raise UserError(_("Please describe the work done."))
            photos = self._files("work_photos")
            if not photos and not job.work_photo_ids:
                raise UserError(_("Please upload at least one completion photo."))
            job._portal_submit_work({
                "work_start_date": self._date(post.get("work_start_date")),
                "work_end_date": self._date(post.get("work_end_date")) or fields.Date.today(),
                "work_notes": notes,
            }, photos)
        return self._handle(job_id, access_token, action,
                            _("Thank you. The completed work has been submitted for review."))

    @http.route("/contractor/job/<int:job_id>/invoice", type="http", auth="public",
                website=True, methods=["POST"])
    def contractor_invoice(self, job_id, access_token=None, **post):
        def action(job):
            documents = self._files("invoice_documents")
            if not documents:
                raise UserError(_("Please attach the invoice."))
            ref = self._text(post.get("invoice_ref"))
            if not ref:
                raise UserError(_("Please enter the invoice number."))
            job._portal_submit_invoice({
                "invoice_ref": ref,
                "invoice_date": self._date(post.get("invoice_date")) or fields.Date.today(),
                "invoice_amount": self._float(post.get("invoice_amount")),
            }, documents)
        return self._handle(job_id, access_token, action,
                            _("Your invoice has been received. Thank you."))
