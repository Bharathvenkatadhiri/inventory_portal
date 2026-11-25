# --- The signed-in app (frontend/), built once into static files -----------------
FROM node:20-alpine AS frontend
WORKDIR /frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/apps/web/package.json apps/web/
COPY frontend/packages/api/package.json packages/api/
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# --- Django ------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
# Served at /app/ (organizations.views.app_shell) and /static/app/.
COPY --from=frontend /frontend/apps/web/dist /app/frontend/apps/web/dist

EXPOSE 8000

# Workers, threads and bind address: see gunicorn.conf.py.
CMD ["gunicorn", "core.wsgi:application"]
