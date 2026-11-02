# -*- coding: utf-8 -*-
"""Share by WhatsApp without the Business API: opens WhatsApp (desktop app,
WhatsApp Web or phone) with the contact and the message pre-filled; the user
just presses Send.

System parameter ``helpdesk_contractor.whatsapp_web`` = 1 forces WhatsApp Web
(web.whatsapp.com) instead of letting the browser choose app / web."""
import re
from urllib.parse import quote

from odoo import _, fields, models
from odoo.exceptions import UserError


class HcWhatsappMixin(models.AbstractModel):
    _name = "hc.whatsapp.mixin"
    _description = "Share by WhatsApp (click to chat)"

    def _wa_phone(self, partner):
        """International number, digits only (e.g. 96891234567)."""
        raw = ""
        for candidate in (partner, partner.commercial_partner_id):
            raw = getattr(candidate, "mobile", False) or candidate.phone or ""
            if raw:
                break
        if not raw:
            raise UserError(_("Add a mobile / phone number for %s first.", partner.display_name))
        digits = re.sub(r"\D", "", raw)
        if raw.strip().startswith("+"):
            return digits
        if digits.startswith("00"):
            return digits[2:]
        country = partner.country_id or partner.commercial_partner_id.country_id \
            or self.env.company.country_id
        code = str(country.phone_code or "968")
        if digits.startswith(code) and len(digits) > len(code) + 6:
            return digits
        return code + digits.lstrip("0")

    def _wa_action(self, partner, message):
        phone = self._wa_phone(partner)
        web = self.env["ir.config_parameter"].sudo().get_param("helpdesk_contractor.whatsapp_web")
        base = "https://web.whatsapp.com/send" if web in ("1", "True", "true") \
            else "https://api.whatsapp.com/send"
        return {
            "type": "ir.actions.act_url",
            "url": f"{base}?phone={phone}&text={quote(message)}",
            "target": "new",
        }


class HelpdeskContractorJob(models.Model):
    _name = "helpdesk.contractor.job"
    _inherit = ["helpdesk.contractor.job", "hc.whatsapp.mixin"]

    def _whatsapp_message(self):
        self.ensure_one()
        self._portal_ensure_token()
        company = self.company_id or self.env.company
        lines = [
            _("Hello %s,", self.contractor_id.name),
            "",
            _("%(company)s has a job for you: %(ref)s", company=company.name, ref=self.name),
        ]
        if self.ticket_id:
            lines.append(_("Ticket: %s", self.ticket_id.name))
        if self.scope:
            lines += ["", _("Scope / Instructions:"), self.scope.strip()]
        if self.site_details:
            lines += ["", _("Site:"), self.site_details.strip()]
        lines += ["", _("Please open this link to accept, submit your quotation and update the job:"),
                  self.portal_link or ""]
        if self.user_id:
            lines += ["", _("Regards,"), self.user_id.name, company.name]
        return "\n".join(lines)

    def action_send_whatsapp(self):
        self.ensure_one()
        action = self._wa_action(self.contractor_id, self._whatsapp_message())
        if self.state in ("draft", "declined"):
            self.write({"state": "sent", "sent_date": fields.Datetime.now(), "decline_reason": False})
        self.message_post(body=_("Job details shared with %s by WhatsApp.", self.contractor_id.name),
                          subtype_xmlid="mail.mt_note")
        return action