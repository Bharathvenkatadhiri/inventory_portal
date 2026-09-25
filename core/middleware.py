# middleware.py
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse


class HtmxMessagesMiddleware:
    """htmx swaps only a fragment of the page, so a message queued during an
    htmx request (e.g. "Marked 'Machining' complete.") had nowhere to show
    and stayed in the session until the next full page — often after
    logout. This appends queued messages to the htmx response as an
    out-of-band swap into #flash-messages, which also consumes them."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            getattr(request, 'htmx', False)
            and response.status_code == 200
            and not response.streaming
            and 'HX-Redirect' not in response
            and response.get('Content-Type', '').startswith('text/html')
        ):
            queued = list(messages.get_messages(request))
            if queued:
                response.content += render_to_string(
                    '_flash_messages.html', {'messages': queued, 'oob': True}, request=request,
                ).encode(response.charset)
        return response


class GlobalSearchMiddleware:
    """Redirects any request carrying a `?search=` query param to the
    marketplace global search view, regardless of which page it was
    submitted from."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        search_query = request.GET.get('search')
        global_search_url = reverse('global_search_view')
        if search_query and request.path != global_search_url:
            query_string = urlencode({'search': search_query})
            search_url = f'{global_search_url}?{query_string}'
            return redirect(search_url)
        response = self.get_response(request)
        return response
