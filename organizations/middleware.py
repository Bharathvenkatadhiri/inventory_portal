from django.contrib import messages
from django.shortcuts import redirect

# The Django pages an ERP member can open: the app (/app), its API, the
# account pages and the public site. The marketplace pages assume a buyer
# or supplier profile, which ERP organizations don't have.
MEMBER_URL_NAMES = {
    'home', 'erp-app', 'erp-app-path', 'org-invitation-accept', 'billing-mock-checkout', 'billing-erp-return',
    'billing-webhook', 'logout', 'reauth', 'password_change', 'password_change_verify',
    'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete',
    'portal-feedback', 'about', 'how-it-works', 'features', 'pricing', 'contact', 'privacy-policy',
    'terms-of-service', 'careers', 'supplier-code-of-conduct',
}
MEMBER_NAMESPACES = {'api-v1', 'admin'}


class WorkspaceScopeMiddleware:
    """Keeps people who signed up as ERP members (User.role 'member') on the
    pages that work for them, instead of a marketplace page that errors."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = request.user
        if not user.is_authenticated or user.role != 'member' or user.is_staff:
            return None
        match = request.resolver_match
        if match is None or match.url_name is None or match.namespace in MEMBER_NAMESPACES:
            return None
        if match.url_name in MEMBER_URL_NAMES:
            return None
        messages.info(request, "That page isn't part of your workspace yet.")
        return redirect('home')
