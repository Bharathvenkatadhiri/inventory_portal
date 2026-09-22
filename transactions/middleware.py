# middleware.py
from django.shortcuts import redirect
from django.urls import reverse
from urllib.parse import urlencode
from django.utils.deprecation import MiddlewareMixin
from transactions.models import Demand, Quote
from accounts.models import Supplier_details, Customer


class GlobalSearchMiddleware:
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


class UserContextMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if request.user.is_authenticated:
            user = request.user
            if user.is_staff:
                user_type = 'manufacturer'
            else:
                user_type = 'customer'

            active_rfqs = RFQ.objects.filter(status='active')
            supplier = Supplier_details.objects.filter(user=user).first()
            customer = Customer.objects.filter(user=user).first()
            active_demands = Demand.objects.filter(user = user, end_date__gte=timezone.now(), quote_id=0, is_deleted=False)
            active_quote = Quote.objects.filter(supplier = supplier, status=None , is_deleted=False)
            user_context = {
                'user_type': user_type,
                'active_rfq': active_demands,
                'active_quote': active_quote,
                'supplier': supplier,
                'customer': customer
            }
        else:
            user_context = None

        request.user_context = user_context

        return None




