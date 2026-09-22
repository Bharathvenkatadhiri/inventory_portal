# ManufactureHub

ManufactureHub is a B2B manufacturing marketplace: a consumer submits a requirement (with a diagram/spec file), manufacturers respond with quotes, the consumer selects one, and the order proceeds through production to payment.

Stack: Django 5.1 + PostgreSQL, server-rendered templates (htmx/Alpine, no separate SPA), a scoped DRF API for webhooks/integrations, Celery + Redis for async work, S3-compatible storage for uploaded files, and Razorpay for payments.

## Environments

This repo keeps **dev** and **prod** configuration deliberately separate, both for the
`.env` file and for Docker Compose, so you never accidentally run production against dev
settings (or vice versa):

| | Env template | Compose file(s) | Notes |
|---|---|---|---|
| Development | `.env.dev.example` | `docker-compose.yml` (+ `docker-compose.override.yml`, auto-merged) | Bundles Postgres/Redis containers, hot-reload volume mount, `runserver` |
| Production | `.env.prod.example` | `docker-compose.prod.yml` (standalone) | No bundled DB/Redis — points at managed instances; runs `migrate` + `collectstatic` + `gunicorn` |

Both `.env.dev.example`/`.env.prod.example` are templates — copy whichever matches your
environment to `.env` (gitignored, never commit it) and fill in the blanks.

## Getting Started (development)

### Prerequisites

- Python 3.12
- Docker + Docker Compose (recommended — runs Postgres/Redis for you)

### Installation (Docker — recommended)

```bash
git clone <repo-url>
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
Node just to run the app. You only need it if you're changing templates/styles:

```bash
npm install
npm run watch-css     # rebuilds on save, during development
npm run build-css     # one-shot minified build — run before committing UI changes
```

### Background workers (Celery)

```bash
celery -A core worker -l info
```

(Started automatically as the `worker` service under Docker Compose, in both dev and prod.)

### Running tests

```bash
pytest
```

## Project layout

- `core` — custom user model (email-based auth, `role`: consumer/manufacturer/admin), settings, Celery app
- `accounts` — `ConsumerProfile`, `ManufacturerProfile`, subscription plans
- `marketplace` — `Requirement`, `RequirementPart`, `Quote`, `Order` (state machine), `OrderStatusHistory`
- `payments` — `Payment`, Razorpay webhook endpoint
- `homepage` — public landing pages

## Deploying (production)

```bash
cp .env.prod.example .env   # fill in every value — real secret key, managed DB/Redis
                             # URLs, S3 bucket, live Razorpay keys, your real domain(s)
docker compose -f docker-compose.prod.yml up -d --build
```

This runs `migrate`, `collectstatic`, and `gunicorn` on startup — no bundled Postgres/Redis
containers (point `.env` at managed instances instead; see the comments in
`.env.prod.example` for why). `docker-compose.prod.yml` is intentionally standalone, not
layered on top of `docker-compose.yml` — the dev file hardcodes the bundled db/redis
container hostnames, which would silently clobber your real production `DATABASE_URL`/
`REDIS_URL` if the two were combined.

Static files use `whitenoise`'s manifest storage, which resolves `{% static %}` tags via a
hashed-filename manifest — that's what `collectstatic` builds. `DEBUG=True` (dev) bypasses
this automatically, which is why it's easy to forget; skipping `collectstatic` with
`DEBUG=False` makes every page referencing `{% static %}` error.

## Roadmap (not yet built)

- Live Razorpay integration (currently a signature-verifying webhook stub)
- AI-assisted diagram/spec extraction (`Requirement.extracted_data` is reserved for this)
- Production deployment (hosting, CI/CD, monitoring)

## Licensing and Commercial Terms

ManufactureHub is a commercial software product. Usage of this application is chargeable, and the source code is available under separate commercial terms. For licensing and purchasing details, please contact bharathvenkatadhiri@gmail.com.

## Contact

For any questions or feedback, please contact us at [bharathvenkatadhiri@gmail.com](mailto:bharathvenkatadhiri@gmail.com).
