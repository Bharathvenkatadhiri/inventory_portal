import os
from pathlib import Path

import environ

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
    "core.middleware.GlobalSearchMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL"),
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
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
else:
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"
    STORAGES["default"] = {"BACKEND": "django.core.files.storage.FileSystemStorage"}

SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 3600

AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = [
    "core.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
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
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="ManufactureHub <no-reply@manufacturehub.example>")

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

# --- Subscription plans ---------------------------------------------------
# Referenced by accounts.forms/accounts.views when creating/updating a
# SubscriptionPlan. Minimal defaults; adjust pricing/limits as the product
# requires.
subscription_plan_details = {
    "basic": {"price": 0, "rfq_limit": "5"},
    "standard": {"price": 999, "rfq_limit": "50"},
    "enterprise": {"price": 4999, "rfq_limit": "unlimited"},
}
