# MakeSetu

MakeSetu is a B2B manufacturing marketplace: a consumer submits a requirement (with a diagram/spec file), manufacturers respond with quotes, the consumer selects one, and the order proceeds through production to payment.

Stack: Django 5.1 + PostgreSQL, server-rendered templates (htmx/Alpine, no separate SPA), S3-compatible storage for uploaded files.

## Environments

This repo keeps **dev** and **prod** configuration deliberately separate, both for the
`.env` file and for Docker Compose, so you never accidentally run production against dev
settings (or vice versa):

| | Env template | Compose file(s) | Notes |
|---|---|---|---|
| Development | `.env.dev.example` | `docker-compose.yml` (+ `docker-compose.override.yml`, auto-merged) | Bundles a Postgres container, hot-reload volume mount, `runserver` |
| Test/staging | `.env.test.example` | `docker-compose.test.yml` (standalone) | For a single EC2 instance: bundles Postgres + an nginx reverse proxy, local-disk media instead of S3, plain HTTP (no certificate) |
| Production | `.env.prod.example` | `docker-compose.prod.yml` (standalone) | No bundled DB — points at a managed instance; runs `migrate` + `collectstatic` + `gunicorn`; expects a real load balancer/certificate in front of it |

Each `.env.*.example` is a template — copy whichever matches your environment to `.env`
(gitignored, never commit it) and fill in the blanks.

## Getting Started (development)

### Common setup

```bash
git clone https://github.com/yourusername/inventory_portal.git
cd inventory_portal
cp .env.dev.example .env   # use this for local dev, whether you run Docker or not
```

This same setup applies to both the Docker and non-Docker workflows below. For production deployment, you later switch to `.env.prod.example` instead.

### Prerequisites

- Python 3.12
- Docker + Docker Compose (recommended — runs Postgres for you)

### Installation (Docker — recommended)

```bash
docker compose up --build
```

In another terminal:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Visit `http://127.0.0.1:8000`. Code changes on the host reload automatically
(`docker-compose.override.yml` bind-mounts the repo into the container).

### Installation (without Docker)

Requires a local PostgreSQL instance and a `DATABASE_URL` in `.env` pointing to it.

**Install PostgreSQL** (skip if you already have it):

- **Windows**: download the installer from https://www.postgresql.org/download/windows/
  and run it (the EDB installer bundles `pgAdmin` too). During setup you'll set a password
  for the `postgres` superuser — remember it, you'll need it below. It installs as a
  Windows service and starts automatically.
- **macOS**: `brew install postgresql@16 && brew services start postgresql@16`
- **Linux (Debian/Ubuntu)**: `sudo apt install postgresql && sudo systemctl start postgresql`

**Create the app user and database** matching `.env.dev.example`'s defaults (user
`makesetu_app`, password `makesetu_app`, database `makesetu`, port `5432` — the same names
the test site uses) — adjust the `DATABASE_URL` in your `.env` instead if you'd rather use
different values or an existing Postgres setup:

```bash
# Windows: open "SQL Shell (psql)" from the Start menu, or run psql from the install dir
# macOS/Linux: just `psql postgres`
psql -U postgres -c "CREATE ROLE makesetu_app LOGIN PASSWORD 'makesetu_app' CREATEDB;"
psql -U postgres -c "CREATE DATABASE makesetu OWNER makesetu_app;"
```

(`CREATEDB` is only so the test runner can create its own throwaway `test_makesetu`
database.)

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux

pip install -r requirements-dev.txt
# edit DATABASE_URL/SECRET_KEY in .env if your local Postgres differs

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### Frontend styling (Tailwind)

The compiled CSS (`homepage/static/css/tailwind-built.css`) is committed, so you don't need
Node just to run the app. You only need it if you're changing templates/styles.

**Install Node.js** (skip if you already have it) — version 20 LTS or newer:

- **Windows/macOS**: download the LTS installer from https://nodejs.org/ and run it (npm is
  bundled in).
- **Linux**: use [nvm](https://github.com/nvm-sh/nvm) (`nvm install --lts`) rather than your
  distro's package manager, which often ships an outdated Node.

Confirm it worked: `node --version` (should print v20+) and `npm --version`.

```bash
npm install
npm run watch-css     # rebuilds on save, during development
npm run build-css     # one-shot minified build — run before committing UI changes
```

### Running tests

```bash
pytest
```

## Company teams, roles and plans

Each buyer or manufacturer company has an Owner (whoever registered it). The Owner and any
Admins invite the team: Procurement and Viewers on a buyer company; Sales, Operations and
Viewers on a supplier company. Only the Owner handles the subscription and billing.

Each company is on a plan, Free, Starter or Business, which sets its features, users, monthly
RFQ/quote limits and file storage. Every action is checked as role, then plan feature, then
limit.

- Roles, per side: [docs/roles-and-permissions.md](docs/roles-and-permissions.md), enforced in
  `accounts/team.py` and `accounts/middleware.py`; approvals in `marketplace/approvals.py`.
- Plans: [docs/plans.md](docs/plans.md), defined in `plans/catalog.py` and applied by
  `plans/access.py`.
- Billing (subscriptions, payments, renewals): `billing/`, described in docs/plans.md. Only the
  mock gateway exists so far.
- After deploying, run `python manage.py rebuild_storage_ledger` once, schedule
  `python manage.py send_rfq_digests` daily (Free suppliers' RFQ alerts), and schedule
  `python manage.py process_subscriptions` hourly (renewals, retries, expiries).

## Project layout

- `core` — custom user model (email-based auth, `role`: consumer/manufacturer/admin), settings
- `accounts` — `ConsumerProfile`, `ManufacturerProfile`, subscription plans
- `marketplace` — `Requirement`, `RequirementPart`, `Quote`, `Order` (state machine), `OrderStatusHistory`
- `homepage` — public landing pages, dashboard

## Deploying (production)

```bash
cp .env.prod.example .env   # fill in every value — real secret key, managed DB URL,
                             # S3 bucket, your real domain(s)
docker compose -f docker-compose.prod.yml up -d --build
```

This runs `migrate`, `collectstatic`, and `gunicorn` on startup — no bundled Postgres
container (point `.env` at a managed instance instead; see the comments in
`.env.prod.example` for why). `docker-compose.prod.yml` is intentionally standalone, not
layered on top of `docker-compose.yml` — the dev file hardcodes the bundled db container
hostname, which would silently clobber your real production `DATABASE_URL` if the two were
combined.

Static files use `whitenoise`'s manifest storage, which resolves `{% static %}` tags via a
hashed-filename manifest — that's what `collectstatic` builds. `DEBUG=True` (dev) bypasses
this automatically, which is why it's easy to forget; skipping `collectstatic` with
`DEBUG=False` makes every page referencing `{% static %}` error.

## Deploying (test site on EC2)

A disposable test/staging deployment on a single EC2 instance — no managed Postgres, no
S3 bucket, no load balancer in front of it, just this one box:

```bash
cp .env.test.example .env   # fill in SECRET_KEY, ALLOWED_HOSTS (the instance's public
                             # IP or DNS name), POSTGRES_PASSWORD, SITE_URL
docker compose -f docker-compose.test.yml up -d --build
```

This bundles its own Postgres container (unlike prod) and puts nginx (`nginx/test.conf`) in
front of gunicorn on port 80. It serves plain HTTP only — no certificate — so
`.env.test.example` explicitly turns off the HTTPS-only cookie/redirect settings that
`DEBUG=False` would otherwise switch on. Uploaded files go to local disk instead of S3, with
nginx serving `/media/` directly off a volume shared with the `web` container;
`ALLOW_LOCAL_MEDIA_STORAGE=True` is what lets this test site opt out of the check that
otherwise refuses to start without an S3 bucket. Emails (OTP codes, order notifications)
print to `docker compose -f docker-compose.test.yml logs web` by default instead of actually
sending — set real SMTP values in `.env` if you want them delivered.

Make sure your EC2 instance's security group allows inbound traffic on port 80 (and 22 for
SSH) before you test from a browser.

Don't point this compose file at your real domain/production data — it's meant to be
rebuilt or thrown away freely. For an actual production deployment, see the section below.

## Sample companies for GST verification

Dev and the test site use the mock GST provider (`GST_PROVIDER=mock`), which recognises
these fictional companies (defined in `SAMPLE_COMPANIES` in
`gst/services/providers/mock.py`). Enter the GSTIN on the supplier or buyer sign-up step and
click Verify GSTIN; the legal name, address and status come back from the provider, and you
choose the name shown on MakeSetu.

| Use as | Company name | GSTIN | City |
|---|---|---|---|
| Supplier | Sri Lakshmi Precision Engineering Pvt Ltd | `33AABCS1234K1Z7` | Chennai |
| Supplier | Kaveri Castings Pvt Ltd | `27AADCK5678M1Z3` | Pune |
| Supplier | Vega Sheet Metal Works LLP | `29AAFCV2468P1Z9` | Bengaluru |
| Supplier | Rudra Polymers Pvt Ltd | `24AAGCR1357Q1Z2` | Ahmedabad |
| Buyer | Tejas Electronics Pvt Ltd | `36AAHCT9753L1Z4` | Hyderabad |
| Buyer | Northline Infrastructure Ltd | `07AAJCN8642R1Z6` | New Delhi |
| Buyer | Malabar Automation Pvt Ltd | `32AAKCM3141S1Z8` | Kochi |
| Buyer | Haryana Agro Machines Pvt Ltd | `06AALCH2718T1Z5` | Gurugram |

Each GSTIN can be registered once per role (one buyer and one supplier account), so a
company can also be used for both roles. To try the failure paths, use any valid-format GSTIN
not in the table: one ending in `0` is reported **cancelled**, ending in `1` **suspended**, and
starting with `00` **not found**; any other verifies as a generic "BUSINESS <PAN> PRIVATE
LIMITED". Every lookup, including failed ones, is listed under GST verifications in the admin.

## Roadmap (not yet built)

- A real payment gateway (billing works end to end on the mock gateway; adding one means a new
  adapter in `billing/gateways/`)
- AI-assisted diagram/spec extraction
- Background/async work (notifications, quote-expiry timers) — no task queue is wired up
  yet; add Celery + Redis back in when there's an actual task to run
- Production deployment (hosting, CI/CD, monitoring)

## Licensing and Commercial Terms

MakeSetu is a commercial software product. Usage of this application is chargeable, and the source code is available under separate commercial terms. For licensing and purchasing details, please contact makesetu@gmail.com.

## Contact

For any questions or feedback, please contact us at [makesetu@gmail.com](mailto:makesetu@gmail.com).
