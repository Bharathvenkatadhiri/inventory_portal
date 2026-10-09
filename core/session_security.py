"""Session binding and recent-password checks.

A session cookie used to be a bearer token good for up to two weeks
("Remember me"), from any browser, for every action including the ones
that move money. Two protections:

* Binding: at login the session records a hash of the browser's
  User-Agent with its version numbers removed: browser, OS and device
  model, but not their versions. A request carrying that session from a
  different browser, OS or device is logged out — what a copied cookie
  (XSS, a shared machine, a synced profile) looks like in practice. The
  versions are left out because browsers, and the installed app's
  webview, update every few weeks, and an update isn't a new device. IP
  isn't used: it legitimately changes for mobile users all the time. The
  User-Agent can be spoofed, so this only raises the bar; logout,
  password changes and session expiry remain what end a session.
* Recent authentication: awarding a quote, confirming an order was paid,
  and staff changes to other accounts need the password to have been
  entered within REAUTH_WINDOW_SECONDS. Otherwise the user confirms it
  again on /reauth/ and is sent back.
"""
import hashlib
import logging
import re
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode

logger = logging.getLogger(__name__)

AUTH_AT = '_auth_at'
UA_FAMILY_HASH = '_ua_family_hash'
# Sessions from before version numbers were ignored hold a hash of the
# full User-Agent instead (see SessionBindingMiddleware).
LEGACY_UA_HASH = '_ua_hash'

# "Chrome/126.0.6478.122", "Android 14", "iPhone OS 17_5", "NT 10.0".
_VERSION = re.compile(r'\d+(?:[._]\d+)*')


def _ua_hash(request):
    return hashlib.sha256(request.META.get('HTTP_USER_AGENT', '').encode()).hexdigest()


def _ua_family_hash(request):
    family = _VERSION.sub('', request.META.get('HTTP_USER_AGENT', ''))
    return hashlib.sha256(family.encode()).hexdigest()


@receiver(user_logged_in)
def _stamp_session(sender, request, user, **kwargs):
    if request is None or not hasattr(request, 'session'):
        return
    request.session[AUTH_AT] = time.time()
    request.session[UA_FAMILY_HASH] = _ua_family_hash(request)
    request.session.pop(LEGACY_UA_HASH, None)


def mark_recently_authenticated(request):
    request.session[AUTH_AT] = time.time()


def recently_authenticated(request):
    auth_at = request.session.get(AUTH_AT)
    return auth_at is not None and time.time() - auth_at <= settings.REAUTH_WINDOW_SECONDS


def safe_next(request, next_url, fallback='home'):
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return next_url
    return reverse(fallback)


def require_recent_auth(request, next_url):
    """None if the user entered their password recently enough; otherwise
    the response that sends them to confirm it (an HX-Redirect for htmx
    requests, since a plain redirect would be swapped into the fragment),
    returning to `next_url` afterwards."""
    if recently_authenticated(request):
        return None
    url = f"{reverse('reauth')}?{urlencode({'next': safe_next(request, next_url)})}"
    if getattr(request, 'htmx', False):
        response = HttpResponse(status=204)
        response['HX-Redirect'] = url
        return response
    return redirect(url)


class SessionBindingMiddleware:
    """Logs out a session presented by a different browser, OS or device
    than the one it was created in. A session holding only the older
    full-User-Agent hash is checked against that once, then moved to the
    version-free hash, so the change logs no one out and lets no copied
    cookie through. Sessions with neither are given a stamp, rather than
    all being logged out at deploy."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            session = request.session
            if UA_FAMILY_HASH in session:
                bound = session[UA_FAMILY_HASH] == _ua_family_hash(request)
            elif LEGACY_UA_HASH in session:
                bound = session[LEGACY_UA_HASH] == _ua_hash(request)
            else:
                bound = True
            if bound:
                if UA_FAMILY_HASH not in session:
                    session[UA_FAMILY_HASH] = _ua_family_hash(request)
                    session.pop(LEGACY_UA_HASH, None)
            else:
                logger.warning("Session for %s presented from a different browser; logged out", user)
                logout(request)
                messages.info(request, "For your security you've been signed out. Please sign in again.")
                if getattr(request, 'htmx', False):
                    response = HttpResponse(status=204)
                    response['HX-Redirect'] = reverse('login')
                    return response
                return redirect('login')
        return self.get_response(request)
