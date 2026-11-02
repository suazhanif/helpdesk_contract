# Helpdesk Contractor Workflow (Odoo 19)

Assign a helpdesk ticket to an outside contractor / vendor and run the whole job
through a contractor portal: confirmation, site visit and quotation, approval,
work completion and invoice.

## Flow

| # | Who | Where | What happens | Status after |
|---|-----|-------|--------------|--------------|
| 1 | Helpdesk user | Ticket → **Assign Contractor** | Pick any contact/company, scope and site details, send | Awaiting Confirmation |
| 2 | Contractor | Email link → portal | Accept (or decline with a reason) | Accepted – Site Visit |
| 3 | Contractor | Portal | Visit date, findings, site photos, quotation amount + document | Quotation Submitted |
| 4 | Helpdesk user | Job → **Approve / Reject Quotation** | Contractor emailed to start work, or to revise | Work in Progress / Quotation Rejected |
| 5 | Contractor | Portal | Work dates, work done, completion photos | Work Submitted |
| 6 | Helpdesk user | Job → **Approve / Reject Work** | Contractor emailed to upload invoice, or to redo | Awaiting Invoice / Work Rejected |
| 7 | Contractor | Portal | Invoice number, date, amount, file | Invoice Received |
| 8 | Helpdesk user | Job → **Close Job** | Job closed, ticket chatter updated | Closed |

Every contractor step posts on the job and the ticket and gives the job's
**Responsible** a To-Do activity. Every team decision emails the contractor.

## Setup

1. Copy `helpdesk_contractor` into the custom addons path, restart Odoo,
   Apps → Update Apps List → install **Helpdesk Contractor Workflow**.
2. Contractors need an **email** on their contact. Tick **Contractor / Vendor**
   on the contact to find them quickly (set automatically on first use).
3. Outgoing mail must be configured and `web.base.url` must be the public URL,
   or the links in the emails will not open for contractors.

## Where things are

* Helpdesk → **Contractor Jobs** (kanban by status; *Action Needed* filter).
* Ticket form: **Assign Contractor** button, **Contractor Jobs** smart button,
  **Contractor** tab.
* Contact form: **Contractor / Vendor** tick box and **Contractor Jobs** button.
* Emails: Settings → Technical → Email Templates, search "Contractor:".
