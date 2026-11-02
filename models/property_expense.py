# -*- coding: utf-8 -*-
"""Property Expense dashboard: every cost per Compound / Building / Unit.

Sources
* Vendor bills / refunds (posted)          -> stage "Actual"
  - from purchase orders, maintenance requests, landlord rent bills,
    contractor jobs, or entered by hand
* Confirmed purchase orders, not yet billed -> stage "Committed"
* Contractor jobs with an approved quotation / invoice but no vendor bill
                                            -> stage "Committed"
Nature: "Direct" = charged to one unit, "Indirect" = shared by a building /
compound or general (can be changed by hand on the PO / bill)."""
from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

EXPENSE_CATEGORIES = [
    ("purchase", "Purchase"),
    ("maintenance", "Maintenance"),
    ("contractor", "Contractor"),
    ("landlord", "Landlord / Rent"),
    ("utility", "Utilities"),
    ("service", "Services"),
    ("admin", "Administration"),
    ("other", "Other"),
]
EXPENSE_NATURE = [("direct", "Direct"), ("indirect", "Indirect")]


class PropertyExpenseFieldsMixin(models.AbstractModel):
    """Compound / Building / Unit + category + direct/indirect."""
    _name = "property.expense.fields.mixin"
    _description = "Property Expense Fields"

    exp_compound_id = fields.Many2one("property.project", string="Compound", index=True)
    exp_building_id = fields.Many2one(
        "property.sub.project", string="Building", index=True,
        domain="[('property_project_id', '=?', exp_compound_id)]")
    exp_unit_id = fields.Many2one(
        "property.details", string="Unit / Flat", index=True,
        domain="[('subproject_id', '=?', exp_building_id), '|', "
               "('property_project_id', '=?', exp_compound_id), "
               "('subproject_id.property_project_id', '=?', exp_compound_id)]")
    exp_category = fields.Selection(EXPENSE_CATEGORIES, string="Expense Category")
    exp_nature = fields.Selection(EXPENSE_NATURE, string="Direct / Indirect",
                                  compute="_compute_exp_nature", store=True, readonly=False,
                                  help="Direct: charged to one unit. Indirect: shared cost of a "
                                       "building, compound or the whole company.")

    @api.depends("exp_unit_id")
    def _compute_exp_nature(self):
        for rec in self:
            rec.exp_nature = "direct" if rec.exp_unit_id else "indirect"

    @api.onchange("exp_compound_id")
    def _onchange_exp_compound_id(self):
        if self.exp_building_id and self.exp_building_id.property_project_id != self.exp_compound_id:
            self.exp_building_id = False
        if self.exp_unit_id and (self.exp_unit_id.property_project_id
                                 or self.exp_unit_id.subproject_id.property_project_id) != self.exp_compound_id:
            self.exp_unit_id = False

    @api.onchange("exp_building_id")
    def _onchange_exp_building_id(self):
        if self.exp_building_id:
            self.exp_compound_id = self.exp_building_id.property_project_id
        if self.exp_unit_id and self.exp_unit_id.subproject_id != self.exp_building_id:
            self.exp_unit_id = False

    @api.onchange("exp_unit_id")
    def _onchange_exp_unit_id(self):
        if self.exp_unit_id:
            self.exp_building_id = self.exp_unit_id.subproject_id
            self.exp_compound_id = self.exp_unit_id.property_project_id \
                or self.exp_unit_id.subproject_id.property_project_id

    @api.model
    def _exp_vals_from_unit(self, unit):
        unit = unit.sudo()
        return {
            "exp_unit_id": unit.id or False,
            "exp_building_id": unit.subproject_id.id or False,
            "exp_compound_id": (unit.property_project_id or unit.subproject_id.property_project_id).id or False,
        }

    @api.model
    def _exp_complete_vals(self, vals):
        if vals.get("exp_unit_id"):
            full = self._exp_vals_from_unit(self.env["property.details"].browse(vals["exp_unit_id"]))
            for key, value in full.items():
                vals.setdefault(key, value)
        elif vals.get("exp_building_id") and not vals.get("exp_compound_id"):
            building = self.env["property.sub.project"].sudo().browse(vals["exp_building_id"])
            vals["exp_compound_id"] = building.property_project_id.id or False
        return vals

    def _exp_property_vals(self):
        self.ensure_one()
        return {
            "exp_compound_id": self.exp_compound_id.id,
            "exp_building_id": self.exp_building_id.id,
            "exp_unit_id": self.exp_unit_id.id,
            "exp_category": self.exp_category,
            "exp_nature": self.exp_nature,
        }


class PurchaseOrder(models.Model):
    _name = "purchase.order"
    _inherit = ["purchase.order", "property.expense.fields.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._exp_complete_vals(vals)
            vals.setdefault("exp_category", "purchase")
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("exp_unit_id") or (vals.get("exp_building_id") and "exp_compound_id" not in vals):
            vals = self._exp_complete_vals(dict(vals))
        return super().write(vals)

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        vals.update({k: v for k, v in self._exp_property_vals().items() if v})
        return vals


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "property.expense.fields.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._exp_complete_vals(vals)
        moves = super().create(vals_list)
        moves.filtered(lambda m: m.move_type in ("in_invoice", "in_refund"))._exp_fill_from_sources()
        return moves

    def write(self, vals):
        if vals.get("exp_unit_id") or (vals.get("exp_building_id") and "exp_compound_id" not in vals):
            vals = self._exp_complete_vals(dict(vals))
        return super().write(vals)

    @api.model
    def _exp_backfill(self):
        """Run on install / upgrade: link existing vendor bills to their property."""
        self.search([("move_type", "in", ("in_invoice", "in_refund")),
                     ("exp_unit_id", "=", False), ("exp_building_id", "=", False),
                     ("exp_compound_id", "=", False)])._exp_fill_from_sources()
        self.env["purchase.order"].search([("exp_category", "=", False)]).write(
            {"exp_category": "purchase"})

    def _exp_fill_from_sources(self):
        """Bills created by other flows: take the property from their origin."""
        Maintenance = self.env["maintenance.request"].sudo()
        RentBill = self.env["rent.bill"].sudo()
        for move in self:
            if move.exp_unit_id or move.exp_building_id or move.exp_compound_id:
                if not move.exp_category:
                    move.exp_category = "other"
                continue
            vals = {}
            maintenance = Maintenance.search([("bill_id", "=", move.id)], limit=1)
            rent_bill = RentBill.search([("rent_bill_id", "=", move.id)], limit=1)
            order = move.invoice_line_ids.purchase_line_id.order_id[:1]
            if maintenance and maintenance.property_id:
                vals = self._exp_vals_from_unit(maintenance.property_id)
                vals["exp_category"] = "maintenance"
            elif rent_bill and rent_bill.tenancy_id.property_id:
                vals = self._exp_vals_from_unit(rent_bill.tenancy_id.property_id)
                vals["exp_category"] = "landlord"
            elif order and (order.exp_unit_id or order.exp_building_id or order.exp_compound_id):
                vals = {k: v for k, v in order._exp_property_vals().items() if v}
            elif not move.exp_category:
                vals["exp_category"] = "purchase" if order else "other"
            if vals:
                move.write(vals)


class MaintenanceRequest(models.Model):
    _inherit = "maintenance.request"

    def write(self, vals):
        res = super().write(vals)
        if vals.get("bill_id") or vals.get("property_id"):
            self.mapped("bill_id").filtered(
                lambda m: not m.exp_unit_id and m.move_type in ("in_invoice", "in_refund")
            )._exp_fill_from_sources()
        return res


class RentBill(models.Model):
    _inherit = "rent.bill"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped("rent_bill_id").filtered(lambda m: not m.exp_unit_id)._exp_fill_from_sources()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get("rent_bill_id"):
            self.mapped("rent_bill_id").filtered(lambda m: not m.exp_unit_id)._exp_fill_from_sources()
        return res


class HelpdeskContractorJob(models.Model):
    _inherit = "helpdesk.contractor.job"

    vendor_bill_id = fields.Many2one("account.move", string="Vendor Bill", readonly=True, copy=False)

    def action_create_vendor_bill(self):
        """Vendor bill from the contractor's invoice, linked to the ticket's unit."""
        self.ensure_one()
        if self.vendor_bill_id:
            return self.action_view_vendor_bill()
        amount = self.invoice_amount or self.quote_amount
        if not amount:
            raise UserError(_("Enter the contractor's invoice amount first."))
        vals = {
            "move_type": "in_invoice",
            "partner_id": self.contractor_id.id,
            "ref": self.invoice_ref or self.name,
            "invoice_date": self.invoice_date or fields.Date.context_today(self),
            "invoice_origin": self.name,
            "exp_category": "contractor",
            "invoice_line_ids": [(0, 0, {
                "name": _("%(job)s - %(ticket)s", job=self.name, ticket=self.ticket_id.name or ""),
                "quantity": 1,
                "price_unit": amount,
            })],
        }
        if self.currency_id:
            vals["currency_id"] = self.currency_id.id
        if self.hd_unit_id:
            vals.update(self.env["account.move"]._exp_vals_from_unit(self.hd_unit_id))
        elif self.hd_building_id or self.hd_compound_id:
            vals.update({"exp_building_id": self.hd_building_id.id,
                         "exp_compound_id": self.hd_compound_id.id})
        bill = self.env["account.move"].create(vals)
        for attachment in self.invoice_attachment_ids:
            attachment.copy({"res_model": "account.move", "res_id": bill.id})
        self.vendor_bill_id = bill
        self.message_post(body=_("Vendor bill %s created.", bill._get_html_link()),
                          subtype_xmlid="mail.mt_note")
        return self.action_view_vendor_bill()

    def action_view_vendor_bill(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.vendor_bill_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }


class PropertyExpenseReport(models.Model):
    _name = "property.expense.report"
    _description = "Property Expenses"
    _auto = False
    _order = "date desc, id desc"
    _rec_name = "reference"

    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Vendor / Contractor", readonly=True)
    compound_id = fields.Many2one("property.project", string="Compound", readonly=True)
    building_id = fields.Many2one("property.sub.project", string="Building", readonly=True)
    unit_id = fields.Many2one("property.details", string="Unit / Flat", readonly=True)
    category = fields.Selection(EXPENSE_CATEGORIES, string="Category", readonly=True)
    nature = fields.Selection(EXPENSE_NATURE, string="Direct / Indirect", readonly=True)
    stage = fields.Selection([("actual", "Actual (Billed)"), ("committed", "Committed (Not Billed)")],
                             string="Stage", readonly=True)
    source = fields.Selection([("bill", "Vendor Bill"), ("purchase", "Purchase Order"),
                               ("contractor", "Contractor Job")], string="Source", readonly=True)
    reference = fields.Char(readonly=True)
    amount = fields.Monetary(string="Amount", readonly=True, help="Untaxed, company currency.")
    move_id = fields.Many2one("account.move", string="Vendor Bill", readonly=True)
    purchase_id = fields.Many2one("purchase.order", string="Purchase Order", readonly=True)
    job_id = fields.Many2one("helpdesk.contractor.job", string="Contractor Job", readonly=True)
    payment_state = fields.Selection([
        ("not_paid", "Not Paid"), ("in_payment", "In Payment"), ("paid", "Paid"),
        ("partial", "Partially Paid"), ("reversed", "Reversed"), ("blocked", "Blocked"),
        ("invoicing_legacy", "Invoicing App Legacy")], string="Payment Status", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                -- Vendor bills and refunds (posted)
                SELECT
                    m.id * 10 + 1 AS id,
                    m.invoice_date AS date,
                    m.company_id,
                    c.currency_id,
                    m.partner_id,
                    m.exp_compound_id AS compound_id,
                    m.exp_building_id AS building_id,
                    m.exp_unit_id AS unit_id,
                    COALESCE(m.exp_category, 'other') AS category,
                    COALESCE(m.exp_nature, CASE WHEN m.exp_unit_id IS NULL THEN 'indirect' ELSE 'direct' END) AS nature,
                    'actual' AS stage,
                    'bill' AS source,
                    COALESCE(NULLIF(m.ref, ''), m.name) AS reference,
                    -m.amount_untaxed_signed AS amount,
                    m.id AS move_id,
                    NULL::integer AS purchase_id,
                    NULL::integer AS job_id,
                    m.payment_state
                FROM account_move m
                JOIN res_company c ON c.id = m.company_id
                WHERE m.move_type IN ('in_invoice', 'in_refund') AND m.state = 'posted'

                UNION ALL

                -- Confirmed purchase orders: part not billed yet
                SELECT
                    po.id * 10 + 2 AS id,
                    po.date_approve::date AS date,
                    po.company_id,
                    c.currency_id,
                    po.partner_id,
                    po.exp_compound_id,
                    po.exp_building_id,
                    po.exp_unit_id,
                    COALESCE(po.exp_category, 'purchase'),
                    COALESCE(po.exp_nature, CASE WHEN po.exp_unit_id IS NULL THEN 'indirect' ELSE 'direct' END),
                    'committed',
                    'purchase',
                    po.name,
                    SUM(pol.price_subtotal * (pol.product_qty - pol.qty_invoiced) / pol.product_qty)
                        / COALESCE(NULLIF(po.currency_rate, 0), 1.0),
                    NULL::integer,
                    po.id,
                    NULL::integer,
                    NULL
                FROM purchase_order po
                JOIN purchase_order_line pol ON pol.order_id = po.id
                JOIN res_company c ON c.id = po.company_id
                WHERE po.state = 'purchase'
                  AND pol.display_type IS NULL
                  AND pol.product_qty > 0
                  AND pol.qty_invoiced < pol.product_qty
                GROUP BY po.id, c.currency_id

                UNION ALL

                -- Contractor jobs approved / invoiced, no vendor bill yet
                SELECT
                    j.id * 10 + 3 AS id,
                    COALESCE(j.invoice_date, j.quote_approved_date::date, j.create_date::date),
                    j.company_id,
                    c.currency_id,
                    j.contractor_id,
                    j.hd_compound_id,
                    j.hd_building_id,
                    j.hd_unit_id,
                    'contractor',
                    CASE WHEN j.hd_unit_id IS NULL THEN 'indirect' ELSE 'direct' END,
                    'committed',
                    'contractor',
                    j.name,
                    COALESCE(NULLIF(j.invoice_amount, 0), j.quote_amount, 0),
                    NULL::integer,
                    NULL::integer,
                    j.id,
                    NULL
                FROM helpdesk_contractor_job j
                JOIN res_company c ON c.id = COALESCE(j.company_id, {self.env.company.id})
                WHERE j.state IN ('quote_approved', 'work_done', 'work_rejected', 'work_approved',
                                  'invoiced', 'done')
                  AND j.vendor_bill_id IS NULL
            )
        """)

    def action_open_source(self):
        self.ensure_one()
        record = self.move_id or self.purchase_id or self.job_id
        return {
            "type": "ir.actions.act_window",
            "res_model": record._name,
            "res_id": record.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }