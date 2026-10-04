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

The lifecycle is in `billing/services.py`; payments go through a gateway adapter (`billing/gateways/`). Only the mock gateway exists so far: its checkout page lets you pay, pay with the webhook delayed, decline, or close.

- **Who.** Only the Owner subscribes, upgrades, downgrades, cancels or pays, on the billing tab. Other roles see the plan.
- **Times.** Subscriptions store exact timestamps: `started_at`, `current_period_start`, `expires_at`. Monthly is 30 days and yearly is 365 days, counted from the moment of payment.
- **Sign-up with a plan.** The pricing page links to `register?plan=…&cycle=…`. The company starts on Free and the billing tab offers "Review and pay" for the plan it picked. Nothing paid is granted before payment.
- **Checkout review.** Anything that's paid for (a new subscription, an upgrade, an overdue renewal) opens a review page first (`billing/checkout/?plan=…&cycle=…`): the price split (for an upgrade, the new plan for the time left less the unused part of the current one), when it starts, when the period ends, the next renewal date and amount, and who it's billed to. The Owner can switch to any other plan or billing cycle there and see its details before paying. Nothing is created until they choose "Proceed to payment", which goes to the gateway's checkout. A scheduled change (downgrade, other cycle) can also be confirmed from the review page.
- **Payment confirmation.** A payment the Owner makes in the portal is confirmed on screen when they return from checkout (plan and expiry); no email is sent for it. Automatic renewals, charged by the hourly job with nobody on the page, are emailed.
- **New subscription** (from Free, or after expiry): full price, and a new period starting when the payment succeeds.
- **Upgrade:** charged straight away for the time left, to the second: (new price − current price) × time left ÷ period length. The higher plan applies at once; the period and expiry stay the same, and usage carries over. A charge under ₹1 (seconds before expiry) applies without a payment. An upgrade keeps the current billing cycle.
- **Downgrade, switch of billing cycle, or Free:** scheduled for the current expiry. The current plan continues until then, and the change can be cancelled.
- **Renewal:** at expiry, the hourly job charges the saved payment method the plan's catalogue price at that moment, or the scheduled plan's price. A price change therefore applies from the next renewal. A retired plan (`RETIRED_PLANS` in `plans/catalog.py`) keeps working until renewal, then renews into its successor.
- **Expiry reminders:** the Owner is emailed 2 days before a paid plan expires (`BILLING_REMINDER_DAYS_BEFORE`) and again on the expiry date. Each email goes once per period. It says what happens next: the plan renews and how much will be charged; it changes to the scheduled plan at that price; or it ends and the company moves to Free, with a link to turn renewal back on. Past-due subscriptions get the failed-payment emails instead.
- **In-portal expiry alert:** over the same window (from 2 days before expiry through the expiry date, local time), everyone on the company account sees a banner on every dashboard page and an entry in the notification bell, until the plan is renewed. Only the Owner gets the "Renew now" link; others are asked to contact the Owner. Closing the banner hides it for that session only, so it shows again at every login. The plan section shows the days left in the current period.
- **Failed renewal:** the subscription becomes past due. The paid plan continues for a 3-day grace period, and the charge is retried 1 and 3 days after expiry (`BILLING_GRACE_DAYS`, `BILLING_RETRY_DAYS`). The Owner can pay from the billing tab at any time. After the last failed retry the company moves to Free. The Owner gets an email at each step.
- **Cancel:** automatic renewal turns off, and the plan continues until expiry, then Free. "Turn renewal back on" before expiry restores it.
- **Payments are applied once.** A payment is applied whether its result arrives by webhook (signed; each event ID is processed once), by the payer's return from checkout (checked with the gateway, never taken from the URL), or by the hourly job. Each payment records the plan and expiry it was priced against. If the subscription has changed by the time it succeeds, the payment isn't applied and is marked "to be refunded" (in Django admin under Billing › Payments). A failure arriving after a success is ignored.
- **Monthly limits** (RFQs, quotes, RFQs received) count in 30-day windows from the start of the current billing period, for Free companies too. A new period or renewal starts a fresh window; an upgrade doesn't.
- **No subscription.** A company without an active subscription is on Free. Staff can still set a plan from the subscriptions screen. A paid plan set there without an expiry stays until changed, and the Owner choosing another paid plan starts a new paid period.
- **Moving to timestamps.** The migration gave each existing paid plan its end date (end of that day) as its expiry, with automatic renewal on. Paid plans with no end date got one period from the migration. With the mock gateway these companies have no saved payment method, so their first renewal fails and the Owner is asked to pay.
- **Earlier plans.** Existing subscriptions moved to the new names when the migration ran: basic → Free, standard → Starter, enterprise → Business, billed monthly at the new prices.

## Running it

- **After deploying:** run `python manage.py rebuild_storage_ledger` once, so files uploaded before storage limits existed count toward storage. New uploads are recorded automatically.
- **Daily RFQ digest:** schedule `python manage.py send_rfq_digests` once a day (e.g. cron `0 7 * * *`). Free suppliers get their RFQ alerts this way; without it they get none.
- **Changing a plan:** edit `plans/catalog.py`. The pricing page and every check follow.
- **Subscriptions:** schedule `python manage.py process_subscriptions` hourly (e.g. cron `0 * * * *`). It sends the expiry reminders and handles renewals, scheduled changes, retries, the end of grace periods, and payments left pending for more than 30 minutes. Without it, nothing renews or expires.
- **Gateway settings:** `BILLING_GATEWAY` (only `mock` for now) and `BILLING_WEBHOOK_SECRET`. Set a long random secret in production. Gateways send webhooks to `/billing/webhook/<gateway>/`. With the mock gateway, `BILLING_MOCK_RENEWAL_RESULT=fail` makes renewals fail, for trying out the past-due flow.
