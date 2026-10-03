# Roles and permissions

What each person on a MakeSetu company account can do, for buyer companies and for manufacturer (supplier) companies.

Roles are only half of it. What a company can do also depends on its **plan** (Free, Starter or Business): features, monthly limits and storage. Plans are described in [plans.md](plans.md). Every action is checked in this order:

1. **Role:** does this person's role allow it? (this page)
2. **Plan:** does the company's plan include the feature?
3. **Limit:** is there room left under the plan's limit (users, RFQs, quotes, storage)?

## Company accounts

- Every company on MakeSetu is one **company account**: a buyer company or a manufacturer company.
- The person who registered the company (and verified its GSTIN) is its **Owner**. Only the Owner manages the subscription and billing, or hands ownership to an Admin.
- The Owner and Admins invite the rest of the team by email. Each person belongs to exactly one company account and one side: one login can't act as both a buyer and a manufacturer.
- Every active person counts toward the plan's **users**, the Owner and Viewers included. Open invitations count too.
- Everything a team creates (RFQs, quotes, orders, messages, documents) belongs to the **company**, not to the individual. If someone is deactivated, their work stays with the company.
- MakeSetu's own staff (platform admins) are separate from all of this and aren't part of any company account.

## Buyer companies: Owner, Admin, Procurement, Viewer

| Permission | Owner | Admin | Procurement | Viewer |
|---|---|---|---|---|
| View company | Yes | Yes | Yes | Yes |
| Edit company | Yes | Yes | No | No |
| Manage users and roles | Yes | Yes | No | No |
| Create and edit RFQs | Yes | Yes | Yes | No |
| Manage quotations (request revisions, reject, change requests) | Yes | Yes | Yes | No |
| Accept a quotation | Yes | Yes | Business plan: needs approval. Other plans: no | No |
| Manage orders | Yes | Yes | Yes | View only |
| Confirm an order payment | Yes | Yes | No | No |
| View invoices | Yes | Yes | Yes | Yes |
| Manage invoices | Yes | Yes | Yes | No |
| Approve or reject acceptance requests | Yes | Yes | No | No |
| Activity log | Yes | Yes | No | No |
| Subscription, billing and payment | Yes | No | No | No |
| Delete company (not built yet) | Yes | No | No | No |

Notes:

- **Viewers are read-only.** Every change a buyer-side Viewer attempts is refused on the server, and the dashboard hides the buttons.
- **Accepting a quotation (or a supplier's new pricing after a change request) is for the Owner and Admins.** On the Business plan, Procurement can ask instead, and the request waits on the **Approvals** page. See [How an approval works](#how-an-approval-works).
- **Invoices** (purchase orders and tax invoices) are issued automatically for each order, so "manage" means running the order that produces them.

## Supplier companies: Owner, Admin, Sales, Operations, Viewer

| Capability | Owner | Admin | Sales | Operations | Viewer |
|---|---|---|---|---|---|
| View dashboard | Yes | Yes | Yes | Yes | Yes |
| Edit company profile (about, contact, capacity, photos, LUT) | Yes | Yes | No | No | No |
| Manage users | Yes | Yes | No | No | No |
| Manage capabilities (capabilities, materials, machines, certifications) | Yes | Yes | Yes | Yes | No |
| View RFQs | Yes | Yes | Yes | Yes | Yes |
| Evaluate RFQs (open, decline) | Yes | Yes | Yes | Yes | Yes |
| Accept an RFQ's NDA | Yes | Yes | Yes | Yes | No |
| Submit, edit and withdraw quotations (and answer change requests) | Yes | Yes | Yes | No | No |
| View buyer information | Yes | Yes | Yes | Yes | Company name only |
| Message buyers | Yes | Yes | Yes | Yes | No |
| Manage orders | Yes | Yes | Yes | Yes | Yes |
| Update production status | Yes | Yes | No | Yes | Yes |
| Update dispatch status | Yes | Yes | No | Yes | Yes |
| Upload production documents (production updates) | Yes | Yes | No | Yes | No |
| Upload quality documents (QC checklist) | Yes | Yes | No | Yes | Yes |
| Manage invoices (e.g. request payment) | Yes | Yes | Yes | Yes | Yes |
| View sales analytics (win rate, quote/response analytics) | Yes | Yes | Yes | No | No |
| View production/order analytics (performance insights) | Yes | Yes | No | Yes | Yes |
| Audit logs | Yes | Yes | Own actions | Own actions | View |
| Manage subscriptions | Yes | No | No | No | No |

The supplier-side Viewer isn't read-only: as the table says, they can update production and dispatch status, quality documents and invoices.

Accepting an NDA and messaging buyers aren't rows in the original table. Accepting an NDA binds the company, so it's limited to the roles that work RFQs; messaging is for everyone who works RFQs or orders.

## How a role is enforced

- Every write to company data is checked against the tables above on the server (`accounts/middleware.py`), whichever page it comes from. Views check as well.
- The dashboard hides controls a role can't use. A buyer Viewer sees a "view-only access" note.

## How an approval works

On the Business plan:

1. A Procurement user accepts a quotation. They see "sent to your company's owner or an admin for approval". No order or purchase order is created yet, and the RFQ page shows the award is waiting for approval.
2. The Owner and every Admin get an email and a notification, and the request appears on their **Approvals** page.
3. The approver clicks **Approve** or **Reject**. Rejecting needs a short reason. Approving asks for the approver's password if they haven't entered it recently.
4. On approval the quotation is accepted exactly as if an Admin had done it. The Procurement user is told either way.

If things changed in the meantime (the RFQ was awarded to someone else, the quote was withdrawn), approving cancels the request instead. If the Owner or an Admin accepts directly, any pending request on that RFQ is cancelled. Requests still pending from before these roles (RFQs, quotes and payments by the old "User" role) can still be decided.

## Team management (Settings → Team)

- **Inviting.** The Owner and Admins enter a name, email and role (the roles of the company's side). The invitee gets an email link, valid for 7 days. An email that already has a MakeSetu account can't be invited.
- **Users.** The plan sets how many people the company can have: Free 2, Starter 5, Business 15, counting everyone (Viewers too) and open invitations. When it's full, inviting and reactivating are refused until someone is deactivated, an invitation is revoked, or the plan is upgraded.
- **Changing roles.** The Owner and Admins can switch anyone except themselves (and the Owner) between their side's roles. Every role counts the same, so a switch never needs room.
- **Deactivating.** A deactivated person can no longer sign in, and stops counting toward users. Their work stays with the company.
- **Handing over ownership.** The Owner can make an active Admin the new Owner (password re-check required). The new Owner takes over the subscription and billing, and the old Owner becomes an Admin.
- **Activity log.** Who sees what is in the tables above. How far back it goes depends on the plan: Free 30 days, Starter 12 months, Business everything, plus filters by person and kind and a CSV export.

## Not covered yet

- **Delete company.** Owner-only in the tables above, but not built.
- **No approval thresholds.** Every acceptance by Procurement needs approval. There's no "only above ₹X" rule yet.
- **Message read status is shared.** It's tracked per company: when a colleague opens a conversation, it counts as read for everyone on that side.
