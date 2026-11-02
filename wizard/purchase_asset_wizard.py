# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _asset_type_selection(self):
    return self.env["account.asset"]._fields["asset_type"].selection


def _asset_category_selection(self):
    return self.env["account.asset"]._fields["asset_category"].selection


class PurchaseAssetCreateWizard(models.TransientModel):
    _name = "purchase.asset.create.wizard"
    _description = "Create Assets from a Purchase Order"

    order_id = fields.Many2one("purchase.order", required=True, readonly=True)
    confirm_order = fields.Boolean(readonly=True)
    # Defaults applied to every line that leaves the field empty
    responsible_id = fields.Many2one("hr.employee", string="Responsible")
    location_id = fields.Many2one("asset.location", string="Location")
    department_id = fields.Many2one("hr.department", string="Department")
    asset_type = fields.Selection(_asset_type_selection, string="Asset Type", default="physical")
    asset_category = fields.Selection(_asset_category_selection, string="Category")
    model_id = fields.Many2one(
        "account.asset", string="Asset Model", domain="[('state', '=', 'model')]",
        help="Accounts and depreciation settings copied onto the new assets.")
    line_ids = fields.One2many("purchase.asset.create.wizard.line", "wizard_id", string="Lines")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env["purchase.order"].browse(
            res.get("order_id") or self.env.context.get("default_order_id"))
        if order:
            res["line_ids"] = [(0, 0, {
                "po_line_id": line.id,
                "name": line.product_id.display_name,
                "quantity": line.product_qty,
            }) for line in order._pending_asset_lines()]
        return res

    def _check_lines(self):
        if not self.line_ids:
            raise UserError(_("There are no lines ticked 'Is an Asset' without an asset."))
        for line in self.line_ids:
            if not (line.location_id or self.location_id):
                raise UserError(_("Set a Location for %s (on the line or at the top).", line.name))

    def action_create(self):
        self.ensure_one()
        self._check_lines()
        order = self.order_id
        if self.confirm_order and order.state in ("draft", "sent", "to approve"):
            order.with_context(skip_asset_wizard=True).button_confirm()
        assets = self.line_ids._create_assets()
        order.message_post(body=_(
            "%(count)s asset(s) created: %(names)s",
            count=len(assets), names=", ".join(assets.mapped("name"))))
        return order.action_view_assets() if assets else {"type": "ir.actions.act_window_close"}

    def action_confirm_without_assets(self):
        """Confirm the order and leave the asset lines for later (Create Assets button)."""
        self.ensure_one()
        if self.order_id.state in ("draft", "sent"):
            self.order_id.with_context(skip_asset_wizard=True).button_confirm()
        return {"type": "ir.actions.act_window_close"}


class PurchaseAssetCreateWizardLine(models.TransientModel):
    _name = "purchase.asset.create.wizard.line"
    _description = "Create Assets from a Purchase Order — Line"

    wizard_id = fields.Many2one("purchase.asset.create.wizard", required=True, ondelete="cascade")
    po_line_id = fields.Many2one("purchase.order.line", required=True, readonly=True)
    product_id = fields.Many2one(related="po_line_id.product_id")
    name = fields.Char(string="Asset Name", required=True)
    quantity = fields.Float(readonly=True)
    one_per_unit = fields.Boolean(
        string="One Asset per Unit",
        help="Ticked: 6 units give 6 assets. Unticked: one asset with Quantity 6.")
    serial_no = fields.Char(string="Serial No.", help="Only used when one asset is created.")
    responsible_id = fields.Many2one("hr.employee", string="Responsible")
    location_id = fields.Many2one("asset.location", string="Location")
    department_id = fields.Many2one("hr.department", string="Department")
    asset_type = fields.Selection(_asset_type_selection, string="Asset Type")
    asset_category = fields.Selection(_asset_category_selection, string="Category")
    model_id = fields.Many2one("account.asset", string="Asset Model",
                               domain="[('state', '=', 'model')]")

    def _create_assets(self):
        Asset = self.env["account.asset"]
        assets = Asset
        for line in self:
            wiz, po_line = line.wizard_id, line.po_line_id
            order, company = po_line.order_id, po_line.order_id.company_id
            date = (order.date_approve or fields.Datetime.now()).date()
            subtotal = order.currency_id._convert(
                po_line.price_subtotal, company.currency_id, company, date)
            unit_price = order.currency_id._convert(
                po_line.price_unit, company.currency_id, company, date)
            qty = max(int(round(po_line.product_qty)), 1)
            count = qty if line.one_per_unit and qty > 1 else 1
            base = {
                "company_id": company.id,
                "acquisition_date": date,
                "purchase_date": date,
                "purchase_price": unit_price,
                "original_value": subtotal / count,
                "purchase_line_id": po_line.id,
                "purchase_order_id": order.id,
                "asset_type": line.asset_type or wiz.asset_type or "physical",
                "asset_category": line.asset_category or wiz.asset_category or False,
                "responsible_id": (line.responsible_id or wiz.responsible_id).id or False,
                "location_id": (line.location_id or wiz.location_id).id or False,
                "department_id": (line.department_id or wiz.department_id).id or False,
                "unit_quantity": 1 if count > 1 else qty,
            }
            model = line.model_id or wiz.model_id
            if model:
                base["model_id"] = model.id
            vals_list = []
            for index in range(count):
                vals = dict(base, name=line.name if count == 1 else f"{line.name} ({index + 1}/{count})")
                if count == 1 and line.serial_no:
                    vals["serial_no"] = line.serial_no
                vals_list.append(vals)
            assets |= Asset.create(vals_list)
        return assets