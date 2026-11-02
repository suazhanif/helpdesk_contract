# -*- coding: utf-8 -*-
"""Compound / Building / Unit on helpdesk tickets (rental_management),
used when assigning a contractor so the site is clear."""
from odoo import _, api, fields, models


class PropertyDetails(models.Model):
    _inherit = "property.details"

    hd_current_tenancy_id = fields.Many2one(
        "tenancy.details", compute="_compute_hd_current_tenancy", string="Current Contract")
    hd_current_tenant_id = fields.Many2one(
        "res.partner", compute="_compute_hd_current_tenancy", string="Current Tenant",
        search="_search_hd_current_tenant")

    def _compute_hd_current_tenancy(self):
        for unit in self:
            tenancy = unit.sudo().tenancy_ids.filtered(
                lambda t: t.contract_type == "running_contract").sorted("start_date", reverse=True)[:1]
            unit.hd_current_tenancy_id = tenancy.id
            unit.hd_current_tenant_id = tenancy.tenancy_id.id

    @api.model
    def _search_hd_current_tenant(self, operator, value):
        tenancies = self.env["tenancy.details"].sudo().search([
            ("contract_type", "=", "running_contract"), ("tenancy_id", operator, value)])
        return [("id", "in", tenancies.property_id.ids)]

    # In the ticket / wizard pickers: "Flat 12 — Tower A / Wattayah Compound",
    # and typing a building, compound or tenant name finds its units.
    @api.depends_context("hd_full_name")
    def _compute_display_name(self):
        super()._compute_display_name()
        if self.env.context.get("hd_full_name"):
            for unit in self:
                parent = " / ".join(p for p in [unit.subproject_id.name, (
                    unit.property_project_id or unit.subproject_id.property_project_id).name] if p)
                if parent:
                    unit.display_name = f"{unit.name} — {parent}"

    @api.model
    def _search_display_name(self, operator, value):
        if self.env.context.get("hd_full_name") and operator == "ilike" and value:
            tenancies = self.env["tenancy.details"].sudo().search([
                ("contract_type", "=", "running_contract"), ("tenancy_id.name", "ilike", value)])
            return ["|", "|", "|", "|",
                    ("name", "ilike", value),
                    ("subproject_id.name", "ilike", value),
                    ("property_project_id.name", "ilike", value),
                    ("subproject_id.property_project_id.name", "ilike", value),
                    ("id", "in", tenancies.property_id.ids)]
        return super()._search_display_name(operator, value)


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    hd_compound_id = fields.Many2one("property.project", string="Compound", tracking=True, index=True)
    hd_building_id = fields.Many2one(
        "property.sub.project", string="Building", tracking=True, index=True,
        domain="[('property_project_id', '=?', hd_compound_id)]")
    hd_unit_id = fields.Many2one(
        "property.details", string="Unit / Flat", tracking=True, index=True,
        domain="[('subproject_id', '=?', hd_building_id), '|', "
               "('property_project_id', '=?', hd_compound_id), "
               "('subproject_id.property_project_id', '=?', hd_compound_id)]")
    hd_unit_floor = fields.Integer(related="hd_unit_id.floor", string="Floor")
    hd_tenancy_id = fields.Many2one(related="hd_unit_id.hd_current_tenancy_id", string="Rent Contract")
    hd_tenant_id = fields.Many2one(related="hd_unit_id.hd_current_tenant_id", string="Tenant")
    hd_location_display = fields.Char(compute="_compute_hd_location_display", store=True,
                                      string="Property Location")

    @api.depends("hd_compound_id", "hd_building_id", "hd_unit_id")
    def _compute_hd_location_display(self):
        for ticket in self:
            parts = [ticket.hd_compound_id.name, ticket.hd_building_id.name, ticket.hd_unit_id.name]
            ticket.hd_location_display = " / ".join(p for p in parts if p) or False

    # ── cascading selection ────────────────────────────────────────────────
    @api.onchange("hd_compound_id")
    def _onchange_hd_compound_id(self):
        if self.hd_building_id and self.hd_building_id.property_project_id != self.hd_compound_id:
            self.hd_building_id = False
        if self.hd_unit_id and (self.hd_unit_id.property_project_id
                                or self.hd_unit_id.subproject_id.property_project_id) != self.hd_compound_id:
            self.hd_unit_id = False

    @api.onchange("hd_building_id")
    def _onchange_hd_building_id(self):
        if self.hd_building_id:
            self.hd_compound_id = self.hd_building_id.property_project_id
        if self.hd_unit_id and self.hd_unit_id.subproject_id != self.hd_building_id:
            self.hd_unit_id = False

    @api.onchange("hd_unit_id")
    def _onchange_hd_unit_id(self):
        if self.hd_unit_id:
            self.hd_building_id = self.hd_unit_id.subproject_id
            self.hd_compound_id = self.hd_unit_id.property_project_id \
                or self.hd_unit_id.subproject_id.property_project_id

    @api.onchange("partner_id")
    def _onchange_partner_hd_unit(self):
        if self.partner_id and not self.hd_unit_id:
            unit = self._hd_unit_from_partner(self.partner_id)
            if unit:
                self.hd_unit_id = unit
                self._onchange_hd_unit_id()

    # ── tickets from the mobile app / email: guess the unit from the tenant ─
    @api.model
    def _hd_unit_from_partner(self, partner):
        partner = partner.commercial_partner_id
        tenancies = self.env["tenancy.details"].sudo().search([
            ("contract_type", "=", "running_contract"),
            ("tenancy_id", "child_of", partner.id),
        ])
        units = tenancies.property_id
        return units if len(units) == 1 else self.env["property.details"]

    @api.model
    def _hd_complete_property_vals(self, vals):
        """Fill building / compound from the unit (or compound from the building)."""
        if vals.get("hd_unit_id"):
            unit = self.env["property.details"].sudo().browse(vals["hd_unit_id"])
            vals.setdefault("hd_building_id", unit.subproject_id.id or False)
            vals.setdefault("hd_compound_id", (unit.property_project_id
                                               or unit.subproject_id.property_project_id).id or False)
        elif vals.get("hd_building_id") and not vals.get("hd_compound_id"):
            building = self.env["property.sub.project"].sudo().browse(vals["hd_building_id"])
            vals["hd_compound_id"] = building.property_project_id.id or False
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("hd_unit_id") and vals.get("partner_id"):
                unit = self._hd_unit_from_partner(self.env["res.partner"].browse(vals["partner_id"]))
                if unit:
                    vals["hd_unit_id"] = unit.id
            self._hd_complete_property_vals(vals)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("hd_unit_id") or (vals.get("hd_building_id") and "hd_compound_id" not in vals):
            vals = self._hd_complete_property_vals(dict(vals))
        return super().write(vals)

    # ── site details for the contractor ─────────────────────────────────────
    def _hd_property_lines(self, compound=None, building=None, unit=None):
        self.ensure_one()
        compound = self.hd_compound_id if compound is None else compound
        building = self.hd_building_id if building is None else building
        unit = self.hd_unit_id if unit is None else unit
        lines = []
        if compound:
            lines.append(_("Compound: %s", compound.name))
        if building:
            lines.append(_("Building: %s", building.name))
        if unit:
            floor = _(" (Floor %s)", unit.floor) if unit.floor else ""
            lines.append(_("Unit / Flat: %(unit)s%(floor)s", unit=unit.name, floor=floor))
            address = ", ".join(p for p in [unit.street, building.street if building else False] if p)
            if address:
                lines.append(_("Address: %s", address))
            tenant = unit.sudo().hd_current_tenant_id
            if tenant:
                phone = tenant.phone or getattr(tenant, "mobile", False)
                lines.append(_("Tenant: %s", f"{tenant.name} ({phone})" if phone else tenant.name))
        return lines

    def _contractor_default_site_details(self, compound=None, building=None, unit=None):
        self.ensure_one()
        lines = self._hd_property_lines(compound, building, unit)
        other = super()._contractor_default_site_details()
        if other:
            lines.append(other)
        return "\n".join(lines)


class HelpdeskContractorJob(models.Model):
    _inherit = "helpdesk.contractor.job"

    hd_compound_id = fields.Many2one(related="ticket_id.hd_compound_id", store=True, string="Compound")
    hd_building_id = fields.Many2one(related="ticket_id.hd_building_id", store=True, string="Building")
    hd_unit_id = fields.Many2one(related="ticket_id.hd_unit_id", store=True, string="Unit / Flat")