from django.shortcuts import render
from django.views.generic import View, TemplateView
from django.contrib.auth.decorators import login_not_required
from marketplace.models import Requirement, Quote
from accounts.models import ManufacturerProfile, ConsumerProfile, SubscriptionPlan
from django.db.models import Count, Q
from datetime import datetime
from django.db.models.functions import ExtractMonth
from django.conf import settings
from django.apps import apps

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)


@login_not_required
def custom_404_view(request, exception):
    return render(request, '404.html', status=404)


@login_not_required
class HomeView(View):
    template_name = "customer_home.html"

    def get_monthly_data(self, request):
        current_year = datetime.now().year
        requirements_no_quotes = Requirement.objects.filter(quote__isnull=True, created_at__year=current_year).annotate(month=ExtractMonth('created_at')).values('month').annotate(count=Count('id')).order_by('month')
        requirements_with_quotes = Requirement.objects.filter(quote__isnull=False, created_at__year=current_year).annotate(month=ExtractMonth('created_at')).values('month').annotate(count=Count('id')).order_by('month')
        requirement_approved = Requirement.objects.filter(status='Approved', created_at__year=current_year).annotate(month=ExtractMonth('created_at')).values('month').annotate(count=Count('id')).order_by('month')
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        no_quotes_data = [0] * 12
        with_quotes_data = [0] * 12
        approved_data = [0] * 12
        for item in requirements_no_quotes:
            no_quotes_data[item['month'] - 1] = item['count']
        for item in requirements_with_quotes:
            with_quotes_data[item['month'] - 1] = item['count']
        for item in requirement_approved:
            approved_data[item['month'] - 1] = item['count']
        data = {
        'series': [
            {'name': 'Requirements with no quotes', 'data': no_quotes_data},
            {'name': 'Requirements with quotes', 'data': with_quotes_data},
            {'name': 'Requirement Approved', 'data': approved_data}
        ],
        'categories': months
        }
        return data

    def get(self, request):
        if not self.request.user.is_authenticated:
            return render(request, "landingpage.html")
        if (self.request.user.is_staff):
            self.template_name = "supplier_home.html"
        overall_requirement_count = Requirement.objects.filter(is_deleted=False).count()
        supplier_count = ManufacturerProfile.objects.filter().count()
        my_requirement_count = Requirement.objects.filter(user=self.request.user).count()
        my_requirement_with_quote_count = Requirement.objects.filter(user=self.request.user, quote__isnull=False).distinct().count()
        requirement_approved_count = Requirement.objects.filter(user=self.request.user, status="Approved").count()
        customer_count = ConsumerProfile.objects.count()
        recent_requirements = Requirement.objects.filter(is_deleted=False).order_by('-created_at')[:5]
        my_recent_requirements = Requirement.objects.filter(user=self.request.user, is_deleted=False)[:5]
        requirements_entity = Requirement.objects.annotate(quote_count=Count('quote'))
        requirements_with_no_quotes = requirements_entity.filter(quote_count=0).count()
        requirements_with_quotes = requirements_entity.filter(quote_count__gt=0).count()
        requirements_approved = requirements_entity.filter(status='Approved').count()
        get_monthly_data_json = self.get_monthly_data(request)
        quotes_with_no_status = Quote.objects.filter(Q(status__isnull=True) | Q(status=''), requirement__user=request.user, is_deleted=False)
        requirement_approved_status = Requirement.objects.filter(is_deleted=False, user=self.request.user, quote__isnull=False).distinct()
        subscription_plan = SubscriptionPlan.objects.filter(user_profile_id=request.user.email).values('plan_type')

        context = {
            'overall_demand_count': overall_requirement_count,
            'supplier_count': supplier_count,
            'my_demand_count': my_requirement_count,
            'my_demand_with_quote_count': my_requirement_with_quote_count,
            'demand_approved_count': requirement_approved_count,
            'customer_count': customer_count,
            'recent_demands': recent_requirements,
            'my_recent_demands': my_recent_requirements,
            'demands_with_no_quotes': requirements_with_no_quotes,
            'demands_with_quotes': requirements_with_quotes,
            'demands_approved': requirements_approved,
            'get_monthly_data_json': get_monthly_data_json,
            'quotes_with_no_status': quotes_with_no_status,
            'demand_approved_status': requirement_approved_status,
            'subscription_plan': subscription_plan[0]['plan_type'] if subscription_plan and not self.request.user.is_superuser else ''
        }

        return render(request, self.template_name, context)


@login_not_required
class AboutView(TemplateView):
    template_name = "about.html"

    def get(self, request):
        context = {'base_template' : 'customer_base.html'}
        if (self.request.user.is_staff):
            context['base_template'] = "supplier_home.html"
        return render(request, self.template_name, context)
