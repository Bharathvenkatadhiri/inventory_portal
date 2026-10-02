# Roles and permissions

What each person on a MakeSetu company account can do, for buyer companies and for manufacturer (supplier) companies.

## Company accounts

- Every company on MakeSetu is one **company account**: a buyer company or a manufacturer company.
- The person who registered the company (and verified its GSTIN) is its **Manager**.
- The Manager invites **Supervisors** and **Users** by email. Each person belongs to exactly one company account and one side: one login can't act as both a buyer and a manufacturer.
- Everything a team creates (RFQs, quotes, orders, messages, documents) belongs to the **company**, not to the individual. If someone is deactivated, their work stays with the company.
- MakeSetu's own staff (platform admins) are separate from all of this and aren't part of any company account.

## Roles at a glance

| | Manager | Supervisor | User |
|---|---|---|---|
| Who | The person who registered the company | Trusted lead, e.g. purchase head or sales head | Day-to-day staff |
| Acts without approval | Yes | Yes | No, for the four actions in [What needs approval](#what-needs-approval) |
| Approves Users' requests | Yes | Yes | No |
| Sees everything the company does | Yes | Yes | Yes |
| Edits other people's RFQs / quotes | Yes | Yes | No, only their own |
| Adds people | Supervisors and Users | Users | No |
| Deactivates people | Supervisors and Users | Users | No |
| Changes someone's role | Yes | No | No |
| Sees the team activity log | Yes | Yes | No |
| Edits company details | Yes | No (read only) | No (read only) |
| Changes the subscription plan | Yes | No (can view) | No (can view) |
| Hands the Manager role to someone else | Yes, to a Supervisor | No | No |

## What needs approval

When a **User** does one of these, it waits on the **Approvals** page until a Supervisor or the Manager approves it. Supervisors and the Manager do them directly.

| Action | Side | What happens while it waits |
|---|---|---|
| Send an RFQ to suppliers | Buyer | The RFQ is saved with the label "Awaiting internal approval". Suppliers can't see it and aren't emailed. |
| Send a quote to the buyer | Supplier | The quote stays a draft. The buyer can't see it. |
| Revise a quote the buyer already has | Supplier | The buyer keeps seeing the current quote. The new price and terms only replace it once approved. |
| Award a quote (including accepting a supplier's new pricing after a change request) | Buyer | No order or purchase order is created yet. The RFQ page shows "Award waiting for approval". |
| Confirm a payment | Buyer | The order stays at "Payment pending". |

## Buyer company: who can do what

| Task | Manager | Supervisor | User |
|---|---|---|---|
| See all the company's RFQs, quotes, orders, messages, documents and reports | Yes | Yes | Yes |
| Post an RFQ | Yes | Yes | Yes, needs approval |
| Edit, move the due date of, or delete an RFQ | Any RFQ | Any RFQ | Only their own. Editing a rejected RFQ sends it for approval again. |
| Change an RFQ that already has quotes (sends a change request to the suppliers) | Yes | Yes | Only their own RFQs |
| Ask a supplier to revise a quote, reject a quote, message suppliers | Yes | Yes | Yes |
| Award a quote | Yes (re-enter password) | Yes (re-enter password) | Yes, needs approval |
| Confirm a payment | Yes (re-enter password) | Yes (re-enter password) | Yes, needs approval |
| Mark an order completed, rate the manufacturer | Yes | Yes | Yes |
| Approve or reject Users' requests | Yes | Yes | No |
| Edit company details | Yes | No | No |
| Change the plan | Yes | No | No |

## Manufacturer company: who can do what

| Task | Manager | Supervisor | User |
|---|---|---|---|
| See the RFQ inbox and all the company's quotes, orders, messages, documents and reports | Yes | Yes | Yes |
| Accept an RFQ's NDA, decline an RFQ, message the buyer | Yes | Yes | Yes |
| Send a quote | Yes | Yes | Yes, needs approval |
| Revise a quote the buyer already has | Yes | Yes | Yes, needs approval |
| Edit or withdraw a quote | Any quote | Any quote | Only their own |
| Answer a buyer's change request with new pricing | Yes | Yes | No (Users can reject the change request) |
| Run an order: start production, production stages, QC checklist, shipment details, production updates, request payment | Yes | Yes | Yes |
| Approve or reject Users' requests | Yes | Yes | No |
| Edit the company profile (about, capabilities, machines, certifications, LUT) | Yes | No (sees the public profile) | No (sees the public profile) |
| Change the plan | Yes | No | No |

## How an approval works

1. A User does one of the actions above. They see "sent to your supervisor or manager for approval".
2. Every Supervisor and the Manager gets an email and a notification, and the request appears on their **Approvals** page. The sidebar shows how many are waiting.
3. The approver opens the request and clicks **Approve** or **Reject**.
   - Rejecting needs a short reason, which is shown to the User.
   - Approving an award or a payment asks for the approver's password if they haven't entered it recently, the same as doing it directly.
4. On approval the action happens exactly as if a Supervisor had done it: the RFQ goes to suppliers, the quote goes to the buyer, the order is created, or the order is marked paid. The User is emailed and notified either way.

Some edge cases:

- **The request no longer makes sense.** If things changed in the meantime (for example the RFQ was awarded to someone else, or the quote was withdrawn), approving it cancels the request instead, with the reason.
- **A Supervisor or the Manager does it directly.** If they act directly on something that has a pending request (for example they award the RFQ themselves), that request is cancelled.
- **Seeing your own requests.** Users see their own requests and their outcomes on the Approvals page. Supervisors and the Manager see the whole company's queue and history.

## Team management (Settings → Team)

- **Inviting.** Enter name, email and role. The invitee gets an email link, valid for 7 days, where they set their name and password, and they join the company straight away. An email that already has a MakeSetu account can't be invited.
- **Team size.** It depends on the Manager's plan. It counts the Manager, active Supervisors and Users, and open invitations.

  | Plan | People on the account |
  |---|---|
  | Basic | 3 |
  | Standard | 10 |
  | Enterprise | Unlimited |

- **Deactivating.** A deactivated person can no longer sign in. Their RFQs, quotes and messages stay with the company. Reactivating needs a free seat.
- **Changing roles.** The Manager can switch anyone between Supervisor and User.
- **Handing over the Manager role.** The Manager can make an active Supervisor the new Manager (password re-check required). The new Manager takes over the plan, and the old Manager becomes a Supervisor.
- **Activity log.** Supervisors and the Manager see a log of what everyone did: RFQs posted, quotes sent, awards, payments, order status changes, approvals and rejections, invitations, role changes and deactivations.

## Not covered yet

- **Change requests aren't approval-gated.** A User's change request on their own RFQ (changing the details of an RFQ that already has quotes) goes to suppliers without approval.
- **No approval thresholds.** Every User action of the four kinds above needs approval. There's no "only above ₹X" rule yet.
- **No narrower view for Users.** Users always see all the company's work. There's no "own items only" mode.
- **Message read status is shared.** It's tracked per company: when a colleague opens a conversation, it counts as read for everyone on that side.
