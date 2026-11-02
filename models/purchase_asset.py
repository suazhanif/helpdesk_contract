# -*- coding: utf-8 -*-
"""Purchase lines flagged as assets create account.asset records
(Asset Management fields: responsible, location, asset type...) through a
wizard shown when the purchase order is confirmed."""
from odoo import _, api, fields, models
from odoo.http import request


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_asset_product = fields.Boolean(
        string="Purchased as Asset",
        help="Purchase order lines for this product are ticked 'Is an Asset' by default, "
             "so an asset is created when the order is confirmed.",
    )


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    is_asset = fields.Boolean(
        string="Is an Asset",
        compute="_compute_is_asset", store=True, readonly=False,
        help="Create an asset for this line when the order is confirmed.",
    )
    asset_ids = fields.One2many("account.asset", "purchase_line_id", string="Assets")

    @api.depends("product_id")
    def _compute_is_asset(self):
        for line in self:
            line.is_asset = bool(line.product_id.product_tmpl_id.is_asset_product)


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    asset_ids = fields.One2many("account.asset", "purchase_order_id", string="Assets")
    asset_count = fields.Integer(compute="_compute_asset_count")
    has_pending_asset_lines = fields.Boolean(compute="_compute_asset_count")

    @api.depends("asset_ids", "order_line.is_asset", "order_line.asset_ids")
    def _compute_asset_count(self):
        for order in self:
            order.asset_count = len(order.asset_ids)
            order.has_pending_asset_lines = bool(order._pending_asset_lines())

    def _pending_asset_lines(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda l: l.is_asset and not l.display_type and l.product_id and not l.asset_ids
        )

    @staticmethod
    def _clicked_confirm_button():
        """True only when the user clicked the Confirm Order button, so automatic
        confirmations (replenishment, other modules) are never interrupted."""
        try:
            path = request.httprequest.path if request else ""
        except RuntimeError:  # no request bound (cron, shell)
            return False
        return "call_button" in path and "button_confirm" in path

    def button_confirm(self):
        if (
            len(self) == 1
            and not self.env.context.get("skip_asset_wizard")
            and self._clicked_confirm_button()
            and self._pending_asset_lines()
        ):
            return self.action_open_asset_wizard(confirm=True)
        return super().button_confirm()

    def action_open_asset_wizard(self, confirm=False):
        self.ensure_one()
        return {
            "name": _("Create Assets"),
            "type": "ir.actions.act_window",
            "res_model": "purchase.asset.create.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_order_id": self.id, "default_confirm_order": confirm},
        }

    def action_view_assets(self):
        self.ensure_one()
        return {
            "name": _("Assets"),
            "type": "ir.actions.act_window",
            "res_model": "account.asset",
            "view_mode": "list,form",
            "domain": [("purchase_order_id", "=", self.id)],
            "context": {"create": False},
        }


class AccountAsset(models.Model):
    _inherit = "account.asset"

    purchase_line_id = fields.Many2one(
        "purchase.order.line", string="Purchase Order Line", readonly=True, copy=False, index=True)
    purchase_order_id = fields.Many2one(
        "purchase.order", string="Purchase Order", readonly=True, copy=False, index=True)