import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # third-party
    "django_htmx",
    "widget_tweaks",
    "storages",
    "django_fsm",
    # project apps
    "core",
    "homepage",
    "accounts",
    "marketplace",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "core.middleware.HtmxMessagesMiddleware",
    "core.middleware.GlobalSearchMiddleware",
    "core.session_security.SessionBindingMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
]

# Views that don't require login. Pair with @login_not_required on the view
# (Django 5.1+) instead of a name-based ignore list where possible.
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "homepage.context_processors.dashboard_sidebar_counts",
                "homepage.context_processors.portal_feedback_prompt",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL"),
}
# Reuse each Postgres connection for up to a minute instead of opening one
# per request — the live-update pollers make many small requests. Health
# checks drop connections the database has closed. Keep
# GUNICORN_WORKERS x GUNICORN_THREADS below the database's connection limit.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# One cache shared by every Gunicorn worker (the notification-bell cache and
# the GST-verification rate limit rely on it). Per-process memory would give
# each worker its own copy, so the rate limit would multiply by the worker
# count. Production defaults to the database cache (no extra service; needs
# `manage.py createcachetable`); set CACHE_URL=redis://... to use Redis.
CACHES = {
    "default": env.cache_url("CACHE_URL", default="locmemcache://" if DEBUG else "dbcache://django_cache"),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Media (user-uploaded requirement diagrams, quotes, etc.) — S3-compatible.
# Falls back to local filesystem storage automatically when no bucket is
# configured (e.g. first run before an S3 bucket exists).
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")
if AWS_STORAGE_BUCKET_NAME:
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="ap-south-1")
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default="") or None
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    # django-storages defaults this to True, which silently replaces an
    # existing object sharing the same key — e.g. two companies' first
    # purchase order both land on order_documents/PO-2627-0001.pdf, and the
    # second overwrites the first. Every upload_to below is now a random
    # name specifically so a collision can't happen even with this off, but
    # keep it off as a second line of defence for any field that isn't.
    AWS_S3_FILE_OVERWRITE = False
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
else:
    # Local disk has two problems in production: it isn't shared across
    # instances/deploys (per the .env.prod.example note this setting
    # already carries), and /media/ is served with no signed-URL check —
    # unlike AWS_QUERYSTRING_AUTH above, so RFQ files, quote files, and
    # purchase order/invoice PDFs (whose names are guessable, e.g.
    # PO-2627-0001.pdf) become fetchable by anyone with the path. Refuse
    # to start rather than silently fall back to that with DEBUG=False.
    # ALLOW_LOCAL_MEDIA_STORAGE is an explicit opt-out for CI, which runs
    # production settings (DEBUG=False) without a bucket; never set it on a
    # real deployment.
    if not DEBUG and not env.bool("ALLOW_LOCAL_MEDIA_STORAGE", default=False):
        raise ImproperlyConfigured(
            "AWS_STORAGE_BUCKET_NAME is required when DEBUG=False. Set it (and the other "
            "AWS_* settings) so uploads use signed S3 URLs instead of unsigned local disk. "
            "(CI only: ALLOW_LOCAL_MEDIA_STORAGE=True skips this check.)"
        )
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"
    STORAGES["default"] = {"BACKEND": "django.core.files.storage.FileSystemStorage"}

# Login brute-force protection (core/login_throttle.py). The per-IP limit
# assumes REMOTE_ADDR is the real client; behind a proxy that doesn't pass
# it through, every user shares one IP, so raise the limit there.
LOGIN_FAILURE_LIMIT_PER_EMAIL = env.int("LOGIN_FAILURE_LIMIT_PER_EMAIL", default=5)
LOGIN_FAILURE_LIMIT_PER_IP = env.int("LOGIN_FAILURE_LIMIT_PER_IP", default=20)
LOGIN_FAILURE_WINDOW_SECONDS = env.int("LOGIN_FAILURE_WINDOW_SECONDS", default=15 * 60)
# The login form says "email not registered" (with a Register link) only for
# an IP's first few failures in the window; after that it gives the generic
# message, so the form can't be used to check which emails have accounts.
LOGIN_UNREGISTERED_HINT_LIMIT = env.int("LOGIN_UNREGISTERED_HINT_LIMIT", default=3)
# Password-reset emails sent per IP and per email address in the same window;
# past either, the form still shows "check your email" but sends nothing.
PASSWORD_RESET_LIMIT_PER_IP = env.int("PASSWORD_RESET_LIMIT_PER_IP", default=5)
PASSWORD_RESET_LIMIT_PER_EMAIL = env.int("PASSWORD_RESET_LIMIT_PER_EMAIL", default=3)
# Awarding a quote, confirming payment and staff changes to other accounts
# need the password to have been entered this recently (core/session_security.py).
REAUTH_WINDOW_SECONDS = env.int("REAUTH_WINDOW_SECONDS", default=15 * 60)

SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 3600

# --- HTTPS / cookie hardening ------------------------------------------------
# On by default whenever DEBUG=False; each can be overridden from the env.
# gunicorn serves plain HTTP, so production always has a TLS-terminating
# proxy/load balancer in front. SECURE_PROXY_SSL_HEADER tells Django to trust
# its X-Forwarded-Proto header — without it, SECURE_SSL_REDIRECT would see
# every request as HTTP and redirect forever. The proxy must overwrite (not
# pass through) any X-Forwarded-Proto the client sends.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=not DEBUG)
if env.bool("SECURE_PROXY_SSL_HEADER", default=not DEBUG):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Start small: browsers cache HSTS for this long, so a mistake (e.g. a
# subdomain still on HTTP) is locked in for that period. Raise to a year
# (31536000) once HTTPS is confirmed working everywhere.
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0 if DEBUG else 3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
# Django's default already, stated explicitly: no page may be framed by any
# site, including this one (nothing here uses iframes).
X_FRAME_OPTIONS = "DENY"

AUTH_USER_MODEL = "core.User"
# Email sign-in only. ModelBackend used to be listed as a fallback, but
# EmailBackend raised on every failure so it never ran; now that
# EmailBackend returns None properly, keeping it would quietly add sign-in
# by username — a second route with its own per-account lockout counter.
# EmailBackend subclasses ModelBackend, so permission checks are unchanged.
AUTHENTICATION_BACKENDS = [
    "core.backends.EmailBackend",
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Logging ---------------------------------------------------------------
# Logs to stdout only (no file handler) — standard for containers, where the
# hosting platform/Docker captures stdout rather than reading files off an
# ephemeral filesystem. LOG_LEVEL defaults to DEBUG in dev, INFO in prod.
LOG_LEVEL = env("LOG_LEVEL", default="DEBUG" if DEBUG else "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# --- Email ------------------------------------------------------------------
# Used for password-reset emails. Defaults to the console backend in DEBUG
# (emails print to stdout, nothing sent) so local dev needs no SMTP setup;
# production must set EMAIL_BACKEND (and the SMTP settings below) via env.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MakeSetu <no-reply@makesetu.com>")
AWS_SES_REGION = env("AWS_SES_REGION", default="ap-southeast-2")
# Base URL used to build absolute links in emails sent outside a request
# (marketplace.emails) — e.g. the RFQ-matched and order-status notifications,
# which fire from award_quote/services rather than always from a view.
# Defaults to the first configured host in production so a real deployment
# that hasn't set SITE_URL explicitly still gets working links rather than
# silently broken (relative, no-domain) ones.
_default_site_url = "http://localhost:8000" if DEBUG else (f"https://{ALLOWED_HOSTS[0]}" if ALLOWED_HOSTS else "")
SITE_URL = env("SITE_URL", default=_default_site_url).rstrip("/")

# --- GST verification -------------------------------------------------------
# "mock" (default) uses accounts.services.gst_verification.MockGSTProvider —
# no external calls, deterministic results, safe for dev/test. Set to a
# registered real-provider name (and add it to get_provider()) to go live;
# real provider credentials belong in env vars only, never in code or
# committed to the frontend.
GST_VERIFICATION_PROVIDER = env("GST_VERIFICATION_PROVIDER", default="mock")
GST_VERIFICATION_API_KEY = env("GST_VERIFICATION_API_KEY", default="")
GST_VERIFICATION_API_BASE_URL = env("GST_VERIFICATION_API_BASE_URL", default="")
GST_VERIFICATION_TIMEOUT_SECONDS = env.int("GST_VERIFICATION_TIMEOUT_SECONDS", default=10)

# --- Buyer/supplier messaging ------------------------------------------------
MESSAGE_ATTACHMENT_MAX_BYTES = env.int("MESSAGE_ATTACHMENT_MAX_BYTES", default=10 * 1024 * 1024)

# --- Subscription plans ---------------------------------------------------
# Referenced by accounts.forms/accounts.views when creating/updating a
# SubscriptionPlan. Minimal defaults; adjust pricing/limits as the product
# requires.
# team_seats counts everyone on the company account — the manager, active
# supervisors/users and pending invitations (accounts.team.seats_used);
# None means unlimited.
subscription_plan_details = {
    "basic": {"price": 0, "rfq_limit": "5", "team_seats": 3},
    "standard": {"price": 999, "rfq_limit": "50", "team_seats": 10},
    "enterprise": {"price": 4999, "rfq_limit": "unlimited", "team_seats": None},
}
