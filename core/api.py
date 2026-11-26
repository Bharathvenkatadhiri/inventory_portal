"""The typed API, /api/v1 (Django Ninja). The React web app and the mobile
app use it; the TypeScript client is generated from its OpenAPI schema.

Every endpoint needs a signed-in session (TabSessionAuth, which also checks
CSRF) unless it says auth=None. Django's LoginRequiredMiddleware is turned
off for these URLs in core/urls.py, because the API answers 401 itself
instead of redirecting to the login page.

All tabs of a browser share one session, so each tab of the web app also
says who it was loaded for (X-User-Id) and which organization it's showing
(X-Organization, see organizations.workspace). When the session no longer
matches (someone else signed in from another tab, or the person lost
access to that organization) the API answers 409 with a `code`, and the
tab reloads instead of acting for the wrong person or organization.
"""
from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI
from ninja.security import SessionAuth

from .api_errors import SessionConflict, SignedInAsSomeoneElse

USER_HEADER = 'HTTP_X_USER_ID'


class TabSessionAuth(SessionAuth):
    """Django session auth (CSRF included), refusing a request from a tab
    that was loaded for a different user than the session now holds."""

    def authenticate(self, request, key):
        user = super().authenticate(request, key)
        expected = request.META.get(USER_HEADER)
        if user is not None and expected and expected != str(user.pk):
            raise SignedInAsSomeoneElse()
        return user


api = NinjaAPI(
    title="MakeSetu API",
    version="1",
    urls_namespace="api-v1",
    auth=TabSessionAuth(),
    docs_decorator=staff_member_required,
)


@api.exception_handler(SessionConflict)
def session_conflict(request, exc):
    return api.create_response(request, {'detail': exc.message, 'code': exc.code}, status=409)


@api.get("/health", auth=None, tags=["platform"])
def health(request):
    return {"status": "ok"}


api.add_router("/", "organizations.api.router")
api.add_router("/", "organizations.app_api.router")
api.add_router("/", "billing.api.router")
api.add_router("/", "inventory.api.router")
api.add_router("/", "purchase.api.router")
api.add_router("/", "sales.api.router")
api.add_router("/", "finance.api.router")
