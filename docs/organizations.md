# Organizations, roles and relationships (ERP)

The ERP is built in three layers. A company is never "a buyer" or "a supplier" as an account type.

1. **Organization.** A company registers once, with its GSTIN. The table is `accounts.Company`: GST identity, PAN, and two switches for the sides it uses:
   - `buying_enabled`: the Purchase module (purchase orders, GRN, purchase bills)
   - `selling_enabled`: the Sales module (quotations, sales orders, invoices, dispatch)

   A manufacturer normally has both. `erp_plan` is the plan chosen at sign-up. Addresses (`organizations.Address`) and bank accounts (`organizations.BankAccount`) hang off the company.
2. **People and roles.** What a person can do inside a company is their **role** there, held through a `Membership`. A role is a set of permission codes (`po.create`, `so.approve`, `grn.post`...). One person can belong to several organizations with a different role in each; the session remembers which one they're working in (`organizations.workspace`).
3. **Relationships.** Who buys from whom is a `BusinessRelationship(buyer, supplier)`. It has a status (invited, pending approval, active, blocked) and the buyer's terms: payment terms, credit limit, vendor and customer codes. Two companies that buy from each other have two rows. Documents between them, such as a purchase order, will carry both organization ids, so the buyer sees the row under Purchase and the supplier sees the same row under Sales.

## Roles

Defined in `organizations/catalog.py`. Every new organization gets these, and can change them later:

| Role | Allows |
|---|---|
| Org Admin | Everything, including company settings, people, roles and partners |
| Purchase Manager | Create and approve POs and purchase bills |
| Purchase Executive | Create POs, enter purchase bills |
| Sales Manager | Quotations, sales orders and their approval, invoices |
| Sales Executive | Quotations and sales orders |
| Store / Warehouse | GRN, stock issue, dispatch |
| Production Planner | BOMs and work orders |
| Accounts | Invoices, purchase bills, payments, ledgers |
| QC | Inspections |
| *Light:* Store Keeper | GRN and stock issue |
| *Light:* Machine Operator | Production quantity done / rejected |
| *Light:* QC Inspector | Inspection pass / fail |
| *Light:* Security / Gate | Gate entry (vehicles in and out) |
| *Light:* Field Sales | View stock, book a sales order (mobile) |

### Full and light users

Every role is a **full** or **light** user type (`Role.user_type`). Light users are shop-floor and support staff who do one or two simple tasks; they cost much less, so factories give everyone their own login instead of sharing one like "store@company". A light role can only hold `catalog.LIGHT_PERMISSIONS` (`grn.post`, `stock.issue`, `stock.view`, `production.record`, `inspection.record`, `gate_entry.record`, `so.create`). The `Role` model refuses anything else, including through `permissions.add()`, and `access.check` refuses it again. Plans count full and light users separately (`organizations.workspace.seat_usage`).

The `Permission` table is synced from `PERMISSIONS` after every `migrate`, so adding a permission needs no data migration.

### Teams

A `Team` is a department inside an organization (Purchase, Sales, Store, Production, Accounts...) with an optional head; `TeamMembership` puts people in it, and one person can be in many teams. The plan limits the number of teams (2 / 5 / unlimited, enforced in `Team.save`). Users are one pool per organization: billing counts each active `Membership` once, never per team (`workspace.seat_usage`). Only members of the organization can join or head its teams. **Rule: roles grant permissions, teams never do.** A team doesn't add to or take away from what its members may do, so being in several teams can't give anyone a confusing mix of permissions. Teams only route and group: a team's approvals go to its head, alerts go to the team, and reports group activity by team (not built yet). To limit what Store staff can do, give them a Store role.

## The permission check

`organizations.access.check(membership, code, counterparty=None)` is the one check for ERP actions. For "create a PO to company B":

1. The user's role in their active organization has `po.create`.
2. That organization has `buying_enabled` (sales permissions need `selling_enabled`), and its plan includes the feature (`plans/erp.py`).
3. If another company is involved, an **active** relationship exists with this organization as the buyer and B as the supplier (the other way round for a sale).

It returns a `Decision` whose `reason` says why not, for the page to show.

## Plans

`plans/erp.py` holds the plans and the pricing table rows (`FEATURE_ROWS`):

| | Starter | Growth | Pro |
|---|---|---|---|
| Price / month | ₹1,500 | ₹4,000 | ₹10,000 |
| Users (shared pool) | 3 | 10 | 25 |
| Light users included | 5 | 20 | 50 |
| Extra light users | ₹150 / month each | ₹150 / month each | ₹150 / month each |
| Teams / departments | 2 | 5 | Unlimited |
| Warehouses | 1 | 3 | 10 |
| Purchase, sales, inventory, GST invoicing, challans/returns/notes, GSTR + Tally, e-way bill, single-level BOM + production entry | ✓ | ✓ | ✓ |
| E-invoice | Add-on | ✓ | ✓ |
| Multi-level BOM + work orders, QC, batch/serial + barcode, job work (ITC-04), production costing + scrap, basic approvals | — | ✓ | ✓ |
| Multi-level approvals, MRP + scheduling, maintenance + audit log | — | — | ✓ |
| Support | Email/chat | Phone | Priority |

An add-on doesn't pass `erp.includes()` until it's bought (not built yet). Billing for ERP plans isn't switched on yet. The older marketplace plans in `plans/catalog.py` still apply to existing buyer and supplier accounts.

## Sign-up

1. **Login** (`register`): name, work email, optional mobile, password. No username or account type; people sign in by email.
2. **Email code** (`verify-email`): the User row is created only once the OTP is confirmed. The code is emailed by a background job.
3. **Company** (`register-organization`): verify the GSTIN (`/api/gst/verify/`), pick buying and/or selling and a plan. The company is built from the server-side GST check, never from posted data, and the person becomes its Org Admin. A GSTIN already on MakeSetu, as an organization or a marketplace account, is refused: the person needs an invitation instead.
4. **Team**: invite people (Settings → People). 5. **Partners**: connect customers and suppliers (Partners).

After signing in, members land in the app at `/app/` (next section). `organizations.middleware.WorkspaceScopeMiddleware` keeps them off the marketplace pages, which assume a buyer or supplier profile.

## The signed-in app

A React app (`frontend/apps/web`, see [frontend/README.md](../frontend/README.md)) at `/app/`, talking to `/api/v1`. Django serves its page (`organizations.views.app_shell`) and assets (`/static/app/`); login, logout, password pages and invitation links stay Django pages.

| Screen | What it does | Who can change things |
|---|---|---|
| Home | Subscription, users / light users / teams against the plan, getting-started steps, what your role allows | — |
| Partners | Customers and suppliers, requests, terms, blocking | `partners.manage` |
| Settings → Company | Name on MakeSetu, buying / selling, findable by name, addresses, bank accounts | `org.manage` |
| Settings → People | Members, roles and teams, switching access off, invitations | `users.manage` |
| Settings → Roles | The starting roles and your own, their permissions | `roles.manage` |
| Settings → Teams | Teams, heads and members | `users.manage` |
| Settings → Billing | Plan, trial, paying, upgrades and downgrades, payments | `billing.manage` |
| My profile, company switcher | Your name and mobile; which company you're working in | yourself |

Every API change checks the permission in the person's active organization (`organizations.app_api.membership_for`), and is refused with 402 while the subscription has lapsed (billing excepted).

## People and invitations

`organizations.people`. An Org Admin (`users.manage`) invites someone by email with a role and, optionally, teams. The link (`/accounts/invitations/<token>/`, valid 7 days, only its hash stored) lets someone new set a password and land in the app; someone already in another organization signs in and accepts, and then switches between companies. Marketplace accounts can't join an organization yet.

- **Seats.** Full users are a hard limit and open invitations hold a seat, so a company can't invite past its plan (`full_user_room`). Light users can go past the number included; each extra one is billed (`billing.erp`). After a downgrade nobody is removed, but nobody new can be added, or switched back on, until it fits.
- **Org Admin.** Every organization keeps at least one active Org Admin. The Org Admin role always has every permission (re-synced after `migrate`) and can't be edited.
- **Switching access off** keeps the membership and everything the person did; they can't open that company. With no active company left, they see a "no access" page.

## Partners

`organizations.partners`. Companies find each other by name (only those that leave **Findable by name** on) or by exact GSTIN.

| How it starts | Status | Who acts next |
|---|---|---|
| We add a supplier on MakeSetu | `invited` | The supplier accepts |
| We add a customer on MakeSetu | `pending_approval` | The buyer approves us as a vendor |
| We add a company not on MakeSetu, by GSTIN | `active` at once | Nobody; we can email them an invitation to join |

Decline (or withdraw) removes a waiting request; either side can block an active one, and only the side that blocked can unblock it. Terms: the buyer sets its vendor code, the supplier its customer code and the credit it gives, and either sets the payment terms.

A company not on MakeSetu is an **unclaimed** `accounts.Company` made from its GST record (no members). When it registers with that GSTIN, sign-up reuses the row, so its relationships are already there. GSTIN ownership isn't proven at sign-up (first to register a GSTIN gets it), the same as before.

## ERP billing

`billing.erp`, with `OrganizationSubscription` and `OrganizationPayment` (`billing/models.py`), on the same payment gateway as marketplace billing (still only the mock one).

- **Trial:** `ERP_TRIAL_DAYS` (14) on the plan chosen at sign-up. Switching plans is free; paying during the trial starts the first month when the trial ends.
- **Price per month:** the plan, plus ₹150 for each light user past the plan's included number, counted when the month is charged.
- **Upgrade:** the plan price difference for the time left, at once. **Downgrade:** at the end of the month. **Cancel:** no renewal, access until the month ends.
- **Renewal:** charged automatically; a failure gives `BILLING_GRACE_DAYS` of access with retries (`BILLING_RETRY_DAYS`), then the subscription lapses.
- **Lapsed:** everything can be read, nothing changed, until someone pays.
- `manage.py process_subscriptions` (hourly) runs trial reminders, trial ends, renewals, retries and lapses for both marketplace and ERP subscriptions.

## API and background jobs

- `/api/v1/` is Django Ninja (`core/api.py`): `organizations/api.py` (public: `health`, `plans`, `roles/defaults`, `permissions`), `organizations/app_api.py` and `billing/api.py` (the app; a signed-in session, CSRF checked). The OpenAPI docs at `/api/v1/docs` are for staff. The schema is exported to `frontend/packages/api/openapi.json`, and CI fails if it's out of date.
- Celery (`core/celery.py`) runs background jobs from Redis (`CELERY_BROKER_URL`). With no broker set, jobs run inline, so tests and a laptop without Redis work unchanged. `docker compose up` starts Redis and a worker.
