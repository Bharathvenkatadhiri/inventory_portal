# ManufactureHub

ManufactureHub is a B2B manufacturing marketplace: a consumer submits a requirement (with a diagram/spec file), manufacturers respond with quotes, the consumer selects one, and the order proceeds through production to payment.

Stack: Django 5.1 + PostgreSQL, server-rendered templates (htmx/Alpine, no separate SPA), a scoped DRF API for webhooks/integrations, Celery + Redis for async work, S3-compatible storage for uploaded files, and Razorpay for payments.

## Getting Started

### Prerequisites

- Python 3.12
- Docker + Docker Compose (recommended — runs Postgres/Redis for you)

### Installation (Docker — recommended)

```bash
git clone <repo-url>
cd inventory_portal
cp .env.example .env   # fill in SECRET_KEY at minimum; defaults work for local dev
docker-compose up --build
```

In another terminal:

```bash
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
```

Visit `http://127.0.0.1:8000`.

### Installation (without Docker)

Requires a local PostgreSQL instance and a `DATABASE_URL` in `.env` pointing to it.

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux

pip install -r requirements-dev.txt
cp .env.example .env         # edit DATABASE_URL/SECRET_KEY

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

(Started automatically as the `worker` service under Docker Compose.)

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

## Production note: static files

Static files use `whitenoise`'s manifest storage, which resolves `{% static %}` tags via
a hashed-filename manifest. In `DEBUG=True` (the local dev default) this is bypassed
automatically. Before deploying with `DEBUG=False`, run `python manage.py collectstatic`
first — otherwise every page referencing `{% static %}` will error.

## Roadmap (not yet built)

- Live Razorpay integration (currently a signature-verifying webhook stub)
- AI-assisted diagram/spec extraction (`Requirement.extracted_data` is reserved for this)
- Production deployment (hosting, CI/CD, monitoring)

## Licensing and Commercial Terms

ManufactureHub is a commercial software product. Usage of this application is chargeable, and the source code is available under separate commercial terms. For licensing and purchasing details, please contact bharathvenkatadhiri@gmail.com.

## Contact

For any questions or feedback, please contact us at [bharathvenkatadhiri@gmail.com](mailto:bharathvenkatadhiri@gmail.com).
