"""Session binding and recent-password checks.

A session cookie used to be a bearer token good for up to two weeks
("Remember me"), from any browser, for every action including the ones
that move money. Two protections:

* Binding: at login the session records a hash of the browser's
  User-Agent. A request carrying that session from a different browser is
  logged out — what a copied cookie (XSS, a shared machine, a synced
  profile) looks like in practice. IP isn't used: it legitimately changes
  for mobile users all the time.
* Recent authentication: awarding a quote, confirming an order was paid,
  and staff changes to other accounts need the password to have been
  entered within REAUTH_WINDOW_SECONDS. Otherwise the user confirms it
  again on /reauth/ and is sent back.
"""
import hashlib
import logging
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
UA_HASH = '_ua_hash'


def _ua_hash(request):
    return hashlib.sha256(request.META.get('HTTP_USER_AGENT', '').encode()).hexdigest()


@receiver(user_logged_in)
def _stamp_session(sender, request, user, **kwargs):
    if request is None or not hasattr(request, 'session'):
        return
    request.session[AUTH_AT] = time.time()
    request.session[UA_HASH] = _ua_hash(request)


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
    """Logs out a session presented by a different browser than the one it
    was created in. Sessions from before this was added have no stamp and
    are given one, rather than all being logged out at deploy."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            expected = request.session.get(UA_HASH)
            current = _ua_hash(request)
            if expected is None:
                request.session[UA_HASH] = current
            elif expected != current:
                logger.warning("Session for %s presented from a different browser; logged out", user)
                logout(request)
                messages.info(request, "For your security you've been signed out. Please sign in again.")
                if getattr(request, 'htmx', False):
                    response = HttpResponse(status=204)
                    response['HX-Redirect'] = reverse('login')
                    return response
                return redirect('login')
        return self.get_response(request)
