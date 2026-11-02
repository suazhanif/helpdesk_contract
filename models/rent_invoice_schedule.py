# -*- coding: utf-8 -*-
"""Rent contract invoices all follow the "Invoice Start From" day.

Example: contract starts 1 Oct, invoices start 20 Oct.
* Every rent / deposit / service invoice of the contract is dated on the 20th
  (20 Oct, 20 Nov, 20 Dec...), including the ones created later by the
  schedulers or the manual installment list.
* Each invoice is due one month after its invoice date (20 Oct -> 20 Nov).
* PDC cheques default to the same day of the month.
"""
import calendar

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

DUE_MONTHS = 1


def _on_billing_day(date, contract):
    """Move a date that the contract built from its start date onto the
    invoice-start day of the same month (1 Nov -> 20 Nov)."""
    if not date or not contract or not contract.invoice_start_date or not contract.start_date:
        return date
    date = fields.Date.to_date(date)
    billing_day = contract.invoice_start_date.day
    start_day = contract.start_date.day
    if billing_day == start_day or date.day != start_day:
        return date
    last_day = calendar.monthrange(date.year, date.month)[1]
    return date.replace(day=min(billing_day, last_day))


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("skip_rent_billing_day"):
            Tenancy = self.env["tenancy.details"].sudo()
            for vals in vals_list:
                if vals.get("move_type") != "out_invoice" or not vals.get("tenancy_id"):
                    continue
                contract = Tenancy.browse(vals["tenancy_id"])
                if vals.get("invoice_date"):
                    vals["invoice_date"] = _on_billing_day(vals["invoice_date"], contract)
                    if not vals.get("invoice_date_due"):
                        vals["invoice_date_due"] = fields.Date.to_date(vals["invoice_date"]) \
                            + relativedelta(months=DUE_MONTHS)
                        # a payment term would recompute the due date
                        vals["invoice_payment_term_id"] = False
        return super().create(vals_list)


class RentInvoice(models.Model):
    _inherit = "rent.invoice"

    @api.model_create_multi
    def create(self, vals_list):
        Tenancy = self.env["tenancy.details"].sudo()
        for vals in vals_list:
            if vals.get("tenancy_id") and vals.get("invoice_date"):
                move = self.env["account.move"].sudo().browse(vals.get("rent_invoice_id") or [])
                vals["invoice_date"] = move.invoice_date if move and move.invoice_date else \
                    _on_billing_day(vals["invoice_date"], Tenancy.browse(vals["tenancy_id"]))
        return super().create(vals_list)


class TenancyDetails(models.Model):
    _inherit = "tenancy.details"

    @api.onchange("invoice_start_date")
    def _onchange_invoice_start_date_pdc_day(self):
        if self.invoice_start_date and "pdc_payment_day" in self._fields:
            self.pdc_payment_day = min(self.invoice_start_date.day, 28)