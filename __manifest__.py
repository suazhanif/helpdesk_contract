{
    "name": "Helpdesk Contractor Workflow",
    "version": "19.0.1.0.0",
    "summary": "Assign helpdesk tickets to contractors/vendors: site visit, quotation, "
               "approval, work completion and invoice through a contractor portal",
    "description": """
Helpdesk Contractor Workflow
============================
* Assign a contractor / vendor (any contact or company) to a helpdesk ticket and
  send it for confirmation by email.
* The contractor works from a secure portal link (no login needed): accepts the
  job, records the site visit with photos, submits a quotation, reports the work
  done with photos and uploads the invoice.
* The helpdesk team is notified at every step, approves or rejects the
  quotation and the completed work, and closes the job.
    """,
    "author": "Baramej® Business and Digital Technology Center LLC",
    "website": "https://baramej.io/en",
    "category": "Services/Helpdesk",
    "license": "OPL-1",
    "depends": ["helpdesk", "portal", "mail", "purchase", "account_asset", "asset_management",
                "rental_management", "rental_management_pdc"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/mail_template_data.xml",
        "data/rent_legal_data.xml",
        "wizard/contractor_wizard_views.xml",
        "wizard/purchase_asset_wizard_views.xml",
        "wizard/rent_legal_wizard_views.xml",
        "views/contractor_job_views.xml",
        "views/helpdesk_ticket_views.xml",
        "views/helpdesk_property_views.xml",
        "views/contractor_job_extra_views.xml",
        "views/property_expense_views.xml",
        "views/res_partner_views.xml",
        "views/portal_templates.xml",
        "views/purchase_asset_views.xml",
        "views/rent_legal_views.xml",
        "views/rent_payment_mode_views.xml",
        "views/pdc_dashboard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
