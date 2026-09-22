from django.shortcuts import render
from django.views.generic import View, TemplateView
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LogoutView
from django.contrib import messages
from marketplace.models import Requirement, Quote, Order
from accounts.models import ManufacturerProfile, ConsumerProfile


@login_not_required
def custom_404_view(request, exception):
    return render(request, '404.html', status=404)


class HomeView(View):
    template_name = "home.html"

    def get(self, request):
        if not request.user.is_authenticated:
            return render(request, "landing.html")

        context = {}

        if request.user.role == "manufacturer":
            context.update({
                "open_requirements": Requirement.objects.filter(is_deleted=False).order_by("-created_at")[:5],
                "open_requirement_count": Requirement.objects.filter(is_deleted=False).count(),
                "my_quotes": Quote.objects.filter(supplier__user=request.user, is_deleted=False).order_by("-created_at")[:5],
                "my_quote_count": Quote.objects.filter(supplier__user=request.user, is_deleted=False).count(),
                "my_orders": Order.objects.filter(supplier__user=request.user).order_by("-created_at")[:5],
                "my_order_count": Order.objects.filter(supplier__user=request.user).count(),
            })
        else:
            my_requirements = Requirement.objects.filter(user=request.user, is_deleted=False)
            context.update({
                "my_requirements": my_requirements.order_by("-created_at")[:5],
                "my_requirement_count": my_requirements.count(),
                "requirements_with_quotes_count": my_requirements.filter(quote__isnull=False).distinct().count(),
                "my_orders": Order.objects.filter(customer__user=request.user).order_by("-created_at")[:5],
                "my_order_count": Order.objects.filter(customer__user=request.user).count(),
            })

        return render(request, self.template_name, context)


class AboutView(TemplateView):
    template_name = "about.html"


class PricingView(TemplateView):
    template_name = "pricing.html"


class ContactView(TemplateView):
    template_name = "contact.html"


class PrivacyPolicyView(TemplateView):
    template_name = "privacy_policy.html"


class TermsOfServiceView(TemplateView):
    template_name = "terms_of_service.html"


class CustomLogoutView(LogoutView):
    next_page = 'home'

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "You've been logged out successfully.")
        return response
