from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from accounts import team

from . import storage


class StorageLimitMiddleware:
    """Refuses an upload that would take the company past its plan's file
    storage (plans.storage), whichever form it comes from — RFQ drawings,
    quote files, message attachments, production updates, photos,
    certificates."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != 'POST' or not request.user.is_authenticated or not request.FILES:
            return None
        if team.company(request.user) is None:
            return None
        uploads = [upload for key in request.FILES for upload in request.FILES.getlist(key)]
        problem = storage.upload_problem(request.user, *uploads)
        if not problem:
            return None
        messages.error(request, problem)
        if getattr(request, 'htmx', False):
            response = HttpResponse(status=413)
            response['HX-Refresh'] = 'true'
            return response
        back = request.META.get('HTTP_REFERER', '')
        if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            back = '/'
        return redirect(back)
