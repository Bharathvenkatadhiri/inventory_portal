# MakeSetu frontend

The signed-in ERP app: React 19 + TypeScript, built with Vite, TanStack Router and Query,
Tailwind. It's served by Django at `/app/` and talks to Django's `/api/v1` with the session
cookie (no separate login). The public site, sign-in and sign-up stay Django pages.

```
frontend/
  apps/web/        the app (pages in src/pages, shared UI in src/components)
  packages/api/    typed client for /api/v1, generated from Django's OpenAPI schema
```

## Running it

Needs Node 20+ and pnpm (`corepack enable`, or `npm i -g pnpm`). Django must be running on
`127.0.0.1:8000` (set `DJANGO_URL` to change that).

```bash
pnpm install
pnpm dev          # http://localhost:5173/app/
```

Sign in at <http://localhost:5173/login/>: in development Vite serves `/app/` and passes every
other path (sign-in, the API, static files, checkout) to Django, so it's one origin, as in
production.

To have Django serve the app itself (as in production), build it: `pnpm build` writes
`apps/web/dist/`, which Django serves at `/app/` and `/static/app/`. The Docker image builds it.

## Checks

```bash
pnpm typecheck
pnpm test         # Vitest + Testing Library
pnpm build
```

## When the API changes

The client's types come from Django's schema, so a change to the API shows up as a type error
here. After changing an endpoint in Django:

```bash
python manage.py export_openapi_schema --api core.api.api --output frontend/packages/api/openapi.json --indent 2 --sorted
pnpm generate-api   # rewrites packages/api/src/schema.ts
```

CI fails if either file is out of date.
