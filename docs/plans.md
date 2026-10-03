# Plans

MakeSetu has three plans, Free, Starter and Business, with separate features for buyer companies and supplier companies. The plan belongs to the company (held on the Owner's account); what each person may do is their **role** ([roles-and-permissions.md](roles-and-permissions.md)). Every action is checked as role → plan feature → limit.

Prices, limits and features are defined once, in `plans/catalog.py`. The pricing page, the billing tab and every check read from there. The public pricing page shows the full tables.

| | Free | Starter | Business |
|---|---|---|---|
| Price / month | ₹0 | ₹999 | ₹2,999 |
| Price / year | ₹0 | ₹9,990 | ₹29,990 |
| Users (everyone, Viewers included) | 2 | 5 | 15 |
| File storage | 1 GB | 5 GB | 25 GB |
| Buyer: RFQs posted / month | 5 | 25 | 100 |
| Supplier: relevant RFQs received / month | 10 | 50 | 200 |
| Supplier: quote submissions / month | 10 | 50 | 200 |

## Limits

- **Users.** Active people on the account (Owner included, every role) plus open invitations. A full plan refuses invitations and reactivations.
- **RFQs posted (buyers).** RFQs created this calendar month, deleted ones included, so deleting and reposting doesn't bypass the limit.
- **RFQs received (suppliers).** Each month a supplier receives up to its quota of open RFQs, best capability match first (`plans/rfq_inbox.py`). The inbox shows received RFQs, alerts only go out for RFQs being received, and opening an RFQ's link receives it while there's quota. Past the quota, further RFQs show as "N more waiting" with an upgrade prompt, and their page is locked.
- **Quote submissions (suppliers).** Quotes first sent to a buyer this month, withdrawn ones included. A quote submitted past the limit is saved as a draft instead, with a message.
- **Storage.** Files the company uploaded: RFQ drawings, quote files, message attachments, production updates, photos, cover image and certificates. Generated POs and invoices don't count. An upload that would go past the limit is refused, whichever form it comes from (`plans/middleware.py`).

Reaching a limit never undoes anything. It only blocks the next one until the month turns or the plan is upgraded. Downgrading works the same way: a team over the new user limit keeps everyone, but can't add or reactivate people until it fits.

## What each feature level means

### Buyer plans

| Feature | Free | Starter | Business |
|---|---|---|---|
| Supplier directory | Browse and keyword search, first 3 pages | + filters (process, certification, city, minimum order), sorting, every page | Same as Starter |
| Supplier matching | — | — | "Suggested manufacturers" on each RFQ, ranked by capability/material match, with "Invite to quote" |
| Quote comparison | Quote cards with Best price / Fastest badges | Same | + side-by-side table: unit price, subtotal, tooling, GST, landed cost, lead time, terms |
| Analytics / reports | Dashboard stats | Dashboard stats | + Reports page (spend, RFQ funnel) |
| Approval workflows | — | — | Procurement can ask to accept a quotation; Owner/Admin approve |
| Order management | Status tracking and documents | + order list filters and search | Same |
| Invoice management | Each order's PO and invoice from the order page | + Documents page with every PO, invoice and file | Same |
| Audit logs | Last 30 days | Last 12 months | Everything, filter by person and kind, CSV export |
| Excel / CSV export | — | RFQs, quotes, orders, invoices, reports, contacts | Same |

### Supplier plans

| Feature | Free | Starter | Business |
|---|---|---|---|
| Supplier profile (what buyers see) | About, contact, capabilities, materials, average rating | + photos, machines, certifications | + cover image, "Exports under LUT" badge, rating breakdown |
| Quote management | Create, edit, withdraw | + My quotes filters and search | + bulk withdraw of drafts, revision history |
| Quote templates | — | Save a quote's terms as a template; start new quotes from one | Same |
| Quote comparison | — | — | Your price vs the winning price on lost RFQs (Reports) |
| Directory listing / search visibility | Listed in browsing and keyword search | + shown in buyers' filtered results | + "Top supplier", listed first |
| Featured profile | — | — | Featured row on the directory and a "Featured" badge |
| Supplier matching | — | Match % in the inbox and dashboard | + recommendations weighted by your win rate per process |
| Matched RFQ recommendations | — | "Recommended for you" on the dashboard | Same |
| RFQ opportunity alerts | Daily email digest | Instant email for each matched RFQ | + alert preferences (processes, materials, minimum quantity) |
| Analytics | Dashboard stats | + Reports page: win rate, quote/response analytics | + performance insights: time to quote, on-time dispatch, rating by month |
| Customer contacts | Buyer company names (Contacts page) | + contact email, phone and conversations | Same |
| Order management, invoices, audit logs, export | As on buyer plans | | |

## Billing

- **Changing plans.** Only the Owner changes the plan, on the billing tab (monthly or yearly). Moving to a smaller plan applies straight away. An upgrade, or a switch to yearly billing, is recorded as a request. There's no payment integration yet, so staff apply it from the subscriptions admin screen once payment is confirmed. The price is always taken from the catalogue.
- **No subscription.** A company without an active subscription is on Free.
- **Earlier plans.** Existing subscriptions moved to the new names when the migration ran: basic → Free, standard → Starter, enterprise → Business, billed monthly at the new prices.

## Running it

- **After deploying:** run `python manage.py rebuild_storage_ledger` once, so files uploaded before storage limits existed count toward storage. New uploads are recorded automatically.
- **Daily RFQ digest:** schedule `python manage.py send_rfq_digests` once a day (e.g. cron `0 7 * * *`). Free suppliers get their RFQ alerts this way; without it they get none.
- **Changing a plan:** edit `plans/catalog.py`. The pricing page and every check follow.
