from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from . import team

# Requests anyone may make that change something: their own session,
# password, notification read-state and feedback about MakeSetu. Nothing
# here touches the company's data.
PERSONAL_URL_NAMES = {
    'login', 'logout', 'reauth', 'password_change', 'password_change_verify', 'billing-renewal-alert-dismiss',
    'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete',
    'notification-open', 'notification-mark-read', 'notification-mark-all-read',
    'portal-feedback', 'gst-verify', 'register', 'verify-email', 'register-supplier', 'register-customer', 'team-join',
}

_ORDER_STATUS_ACTIONS = {
    'in_production': 'order.production',
    'payment_pending': 'invoice.manage',
    'paid': 'order.confirm_payment',
}

# Writes to company data: url name -> the role permission it needs
# (accounts.team.ROLE_PERMISSIONS), or a function of the resolved kwargs.
# Views check too; this is the backstop that makes "forgot to check" safe.
WRITE_ACTIONS = {
    'team-invite': 'team.manage', 'team-invitation-revoke': 'team.manage',
    'team-member-role': 'team.manage', 'team-member-active': 'team.manage',
    'team-transfer': 'subscription.manage',
    'billing-change': 'subscription.manage', 'billing-cancel': 'subscription.manage',
    'billing-reactivate': 'subscription.manage', 'billing-cancel-change': 'subscription.manage',
    'billing-dismiss-intended': 'subscription.manage', 'billing-mock-checkout': 'subscription.manage',
    'company-about-update': 'company.edit', 'company-contact-update': 'company.edit',
    'company-capacity-update': 'company.edit', 'company-lut-update': 'company.edit',
    'company-photo-upload': 'company.edit', 'company-photo-delete': 'company.edit',
    'company-capability-add': 'capabilities.manage', 'company-capability-remove': 'capabilities.manage',
    'company-material-add': 'capabilities.manage', 'company-material-remove': 'capabilities.manage',
    'company-machine-add': 'capabilities.manage', 'company-machine-remove': 'capabilities.manage',
    'company-certification-upload': 'capabilities.manage', 'company-certification-delete': 'capabilities.manage',
    'rfq-alert-preferences': 'rfq.alerts',
    'new-requirement': 'rfq.create', 'edit-requirement': 'rfq.edit', 'delete-requirement': 'rfq.edit',
    'requirement-extend': 'rfq.edit', 'requirement-update-status': 'order.production', 'amendment-withdraw': 'rfq.edit',
    'requirement-decline': 'rfq.evaluate', 'requirement-accept-nda': 'rfq.accept_nda',
    'amendment-respond': 'quotes.manage', 'amendment-decide': 'quotes.manage',
    'new-quote': 'quotes.manage', 'edit-quote': 'quotes.manage', 'delete-quote': 'quotes.manage',
    'quotes-bulk': 'quotes.manage', 'quote-template-save': 'quotes.manage', 'quote-template-delete': 'quotes.manage',
    'quote-update-status': 'quotes.manage', 'quote-request-revision': 'quotes.manage',
    'quote-decline-revision': 'quotes.manage',
    'order-update-status': lambda kwargs: _ORDER_STATUS_ACTIONS.get(kwargs.get('status'), 'order.manage'),
    'order-production-advance': 'order.production', 'order-shipment-update': 'order.dispatch',
    'order-post-update': 'order.production_docs', 'order-qc-toggle': 'order.quality_docs',
    'order-review': 'order.manage',
    'message-thread-start': 'messages.send', 'message-thread-ask-buyer': 'messages.send',
    'message-thread': 'messages.send', 'message-thread-close': 'messages.send', 'message-delete': 'messages.send',
    'approval-decide': 'approvals.decide',
}
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS', 'TRACE')


class RolePermissionMiddleware:
    """Refuses any write to company data the user's role doesn't allow
    (accounts.team.ROLE_PERMISSIONS), whatever the view. A Viewer's write to
    a URL not listed here is refused too: their table is a short list of
    exceptions, so for them anything unlisted is a "no"."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in SAFE_METHODS or not request.user.is_authenticated or team.company(request.user) is None:
            return None
        match = request.resolver_match
        if match is None or match.namespace == 'admin' or match.url_name in PERSONAL_URL_NAMES:
            return None
        action = WRITE_ACTIONS.get(match.url_name)
        if callable(action):
            action = action(view_kwargs)
        if action is None:
            allowed = not team.is_viewer(request.user)
        else:
            allowed = team.allows(request.user, action)
        if allowed:
            return None
        messages.error(request, f"Your role ({team.role_label(request.user)}) on this company account can't do that.")
        if getattr(request, 'htmx', False):
            # Reload, so the message shows and nothing is half-swapped.
            response = HttpResponse(status=403)
            response['HX-Refresh'] = 'true'
            return response
        back = request.META.get('HTTP_REFERER', '')
        if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            back = '/'
        return redirect(back)
