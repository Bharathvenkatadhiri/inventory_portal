"""Brute-force protection for the login form.

Failed logins are counted in Django's cache in two fixed windows: one per
email (stops password guessing against a single buyer or supplier) and one
per client IP (stops one machine spraying guesses, or probing which emails
are registered, across many accounts). Once either count reaches its limit,
further attempts are refused without checking the password until the
window expires. A successful login clears that email's count.

Production uses the database cache, so counts are shared across gunicorn
workers; the dev locmem cache counts per process, which is fine locally.
"""
import hashlib
import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _email_key(email):
    # Hashed so arbitrary user input never ends up in a cache key.
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"login-fail:email:{digest}"


def _ip_key(request):
    return f"login-fail:ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def is_locked_out(request, email):
    if email and cache.get(_email_key(email), 0) >= settings.LOGIN_FAILURE_LIMIT_PER_EMAIL:
        return True
    return cache.get(_ip_key(request), 0) >= settings.LOGIN_FAILURE_LIMIT_PER_IP


def ip_failure_count(request):
    return cache.get(_ip_key(request), 0)


def _increment(key):
    # add() only sets the key if it's missing, so the window starts at the
    # first failure and isn't extended by later ones.
    cache.add(key, 0, timeout=settings.LOGIN_FAILURE_WINDOW_SECONDS)
    try:
        return cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, timeout=settings.LOGIN_FAILURE_WINDOW_SECONDS)
        return 1


def record_failure(request, email):
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    ip_count = _increment(_ip_key(request))
    email_count = _increment(_email_key(email)) if email else 0
    if email_count == settings.LOGIN_FAILURE_LIMIT_PER_EMAIL:
        logger.warning("Login locked for %s after %s failed attempts (last from %s)", email, email_count, ip)
    if ip_count == settings.LOGIN_FAILURE_LIMIT_PER_IP:
        logger.warning("Login locked for IP %s after %s failed attempts", ip, ip_count)


def clear_failures(email):
    if email:
        cache.delete(_email_key(email))
