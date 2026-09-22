# ManufactureHub

ManufactureHub is a B2B manufacturing marketplace: a consumer submits a requirement (with a diagram/spec file), manufacturers respond with quotes, the consumer selects one, and the order proceeds through production to payment.

Stack: Django 5.1 + PostgreSQL, server-rendered templates (htmx/Alpine, no separate SPA), S3-compatible storage for uploaded files.

## Environments

This repo keeps **dev** and **prod** configuration deliberately separate, both for the
`.env` file and for Docker Compose, so you never accidentally run production against dev
settings (or vice versa):

| | Env template | Compose file(s) | Notes |
|---|---|---|---|
| Development | `.env.dev.example` | `docker-compose.yml` (+ `docker-compose.override.yml`, auto-merged) | Bundles a Postgres container, hot-reload volume mount, `runserver` |
| Production | `.env.prod.example` | `docker-compose.prod.yml` (standalone) | No bundled DB — points at a managed instance; runs `migrate` + `collectstatic` + `gunicorn` |

Both `.env.dev.example`/`.env.prod.example` are templates — copy whichever matches your
environment to `.env` (gitignored, never commit it) and fill in the blanks.

## Getting Started (development)

### Prerequisites

- Python 3.12
- Docker + Docker Compose (recommended — runs Postgres for you)

### Installation (Docker — recommended)

```bash
git clone https://github.com/yourusername/inventory_portal.git
cd inventory_portal
cp .env.dev.example .env   # defaults already work for local dev
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

**Create the database** matching `.env.dev.example`'s defaults (user `postgres`, password
`postgres`, database `marketplace`, port `5432`) — adjust the `DATABASE_URL` in your `.env`
instead if you'd rather use different values or an existing Postgres setup:

```bash
# Windows: open "SQL Shell (psql)" from the Start menu, or run psql from the install dir
# macOS/Linux: just `psql postgres`
psql -U postgres -c "CREATE DATABASE marketplace;"
```

(If your `postgres` user's password isn't `postgres`, either set it to match —
`psql -U postgres -c "ALTER USER postgres PASSWORD 'postgres';"` — or edit `DATABASE_URL`
in `.env` after copying the template below to use your real password instead.)

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux

pip install -r requirements-dev.txt
cp .env.dev.example .env     # edit DATABASE_URL/SECRET_KEY if your local Postgres differs

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

## Roadmap (not yet built)

- Payments (no app/model exists yet — deferred until the flow is actually being built)
- AI-assisted diagram/spec extraction
- Background/async work (notifications, quote-expiry timers) — no task queue is wired up
  yet; add Celery + Redis back in when there's an actual task to run
- Production deployment (hosting, CI/CD, monitoring)

## Licensing and Commercial Terms

ManufactureHub is a commercial software product. Usage of this application is chargeable, and the source code is available under separate commercial terms. For licensing and purchasing details, please contact bharathvenkatadhiri@gmail.com.

## Contact

For any questions or feedback, please contact us at [bharathvenkatadhiri@gmail.com](mailto:bharathvenkatadhiri@gmail.com).
