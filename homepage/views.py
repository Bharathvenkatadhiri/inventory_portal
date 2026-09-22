from django.shortcuts import render
from django.views.generic import View, TemplateView
from inventory.models import Stock
from django.db.models import Count
from transactions.models import Demand, Supplier_details, Customer, Quote
from accounts.models import SubscriptionPlan
from django.http import JsonResponse
from django.db.models import Count, Sum, Q
from datetime import datetime
from django.db.models.functions import ExtractMonth
from django.conf import settings
from django.apps import apps

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)

def custom_404_view(request, exception):
    return render(request, '404.html', status=404)



class HomeView(View):
    template_name = "customer_home.html"

    def get_monthly_data(self, request):
        current_year = datetime.now().year
        demands_no_quotes = Demand.objects.filter(quote__isnull=True,created_at__year=current_year).annotate(month=ExtractMonth('created_at')).values('month').annotate(count=Count('id')).order_by('month')
        demands_with_quotes = Demand.objects.filter(quote__isnull=False,created_at__year=current_year).annotate(month=ExtractMonth('created_at')).values('month').annotate(count=Count('id')).order_by('month')
        demand_approved = Demand.objects.filter(status='approved', created_at__year=current_year).annotate(month=ExtractMonth('created_at')).values('month').annotate(count=Count('id')).order_by('month')
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        no_quotes_data = [0] * 12
        with_quotes_data = [0] * 12
        approved_data = [0] * 12
        for item in demands_no_quotes:
            no_quotes_data[item['month'] - 1] = item['count']
        for item in demands_with_quotes:
            with_quotes_data[item['month'] - 1] = item['count']
        for item in demand_approved:
            approved_data[item['month'] - 1] = item['count']
        data = {
        'series': [
            {'name': 'Demands with no quotes', 'data': no_quotes_data},
            {'name': 'Demands with quotes', 'data': with_quotes_data},
            {'name': 'Demand Approved', 'data': approved_data}
        ],
        'categories': months
        }
        return data

    def get(self, request):
        if not self.request.user.is_authenticated:
            return render(request, "landingpage.html")
        if (self.request.user.is_staff):
            self.template_name = "supplier_home.html"
        overall_demand_count = Demand.objects.filter(is_deleted=False).count()
        supplier_count = Supplier_details.objects.filter().count()
        my_demand_count = Demand.objects.filter(user=self.request.user).count()
        my_demand_with_quote_count = Demand.objects.filter(user=self.request.user, quote_id__isnull=False).count()
        demand_approved_count = Demand.objects.filter(user=self.request.user, status="Approved").count()
        customer_count = Customer.objects.count()  # Assuming you have a Customer model
        recent_demands = Demand.objects.filter(is_deleted=False).order_by('-created_at')[:5]
        my_recent_demands = Demand.objects.filter(user=self.request.user, is_deleted=False)[:5]
        demands_entity = Demand.objects.annotate(quote_count=Count('quote'))
        demands_with_no_quotes = demands_entity.filter(quote_count=0).count()
        demands_with_quotes = demands_entity.filter(quote_count__gt=0).count()
        demands_approved = demands_entity.filter(status='Approved').count()
        get_monthly_data_json = self.get_monthly_data(request)
        quotes_with_no_status = Quote.objects.filter( Q(status__isnull=True) | Q(status=''), demand__user=request.user, is_deleted=False )
        demand_approved_status = Demand.objects.filter(is_deleted=False, user=self.request.user,quote_id__gt=0)
        subscription_plan = SubscriptionPlan.objects.filter(user_profile_id=request.user.email).values('plan_type')

        context = {
            'overall_demand_count': overall_demand_count,
            'supplier_count': supplier_count,
            'my_demand_count': my_demand_count,
            'my_demand_with_quote_count': my_demand_with_quote_count,
            'demand_approved_count': demand_approved_count,
            'customer_count': customer_count,
            'recent_demands':recent_demands,
            'my_recent_demands':my_recent_demands,
            'demands_with_no_quotes':demands_with_no_quotes,
            'demands_with_quotes':demands_with_quotes,
            'demands_approved':demands_approved,
            'get_monthly_data_json':get_monthly_data_json,
            'quotes_with_no_status':quotes_with_no_status,
            'demand_approved_status':demand_approved_status,
            'subscription_plan' : subscription_plan[0]['plan_type'] if not self.request.user.is_superuser else ''
        }

        return render(request, self.template_name, context)
class AboutView(TemplateView):
    template_name = "about.html"
    
    def get(self, request):
        context = {'base_template' : 'customer_base.html'}
        if (self.request.user.is_staff):
            context['base_template'] = "supplier_home.html"
        return render(request, self.template_name, context)