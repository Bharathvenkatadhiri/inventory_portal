from django.shortcuts import render, redirect
from django.views.generic import View, TemplateView
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.utils import timezone
from marketplace import services
from marketplace.models import Requirement, Quote, Order
from accounts.models import ManufacturerProfile, ConsumerProfile, SubscriptionPlan
from accounts.views import plan_catalog


@login_not_required
def custom_404_view(request, exception):
    return render(request, '404.html', status=404)


def _subscription_context(user):
    subscription = SubscriptionPlan.objects.filter(user_profile=user).first()
    return {
        'subscription': subscription,
        'plan_catalog': plan_catalog(subscription),
    }


class HomeView(View):
    def get(self, request):
        if not request.user.is_authenticated:
            return render(request, "landing.html")

        if request.user.role == "manufacturer":
            return self._manufacturer_dashboard(request)
        return self._buyer_dashboard(request)

    def _buyer_dashboard(self, request):
        hour = timezone.localtime().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        customer = ConsumerProfile.objects.filter(user=request.user).first()

        open_requirements = services.buyer_open_requirements(request.user)
        pending_quotes = services.buyer_pending_quotes(request.user)
        pending_quotes_value = services.totals_by_currency(
            (q.requirement.quote_currency, q.get_breakdown()['total']) for q in pending_quotes
        )
        active_orders = Order.objects.filter(customer__user=request.user).exclude(status__in=['completed', 'cancelled'])

        quarter_start = services.quarter_start(timezone.now())
        orders_this_quarter = list(
            Order.objects.filter(customer__user=request.user, created_at__gte=quarter_start).select_related('quote', 'supplier', 'requirement')
        )
        spend_this_quarter = services.totals_by_currency(
            (order.requirement.quote_currency, order.quote.get_breakdown()['total']) for order in orders_this_quarter
        )
        suppliers_this_quarter = len({order.supplier_id for order in orders_this_quarter})

        context = {
            "greeting": greeting,
            "customer": customer,
            "stat_open_rfqs": open_requirements.count(),
            "stat_quotes_to_review_count": pending_quotes.count(),
            "stat_quotes_to_review_value": pending_quotes_value,
            "stat_active_orders": active_orders.count(),
            "stat_spend_this_quarter": spend_this_quarter,
            "stat_suppliers_this_quarter": suppliers_this_quarter,
            "recent_requirements": Requirement.objects.filter(user=request.user, is_deleted=False).order_by('-created_at')[:6],
            "action_items": services.buyer_action_items(request.user)[:3],
            "orders_in_progress": active_orders.order_by('ship_by_date')[:5],
        }
        context.update(_subscription_context(request.user))
        return render(request, "home_buyer.html", context)

    def _manufacturer_dashboard(self, request):
        supplier = ManufacturerProfile.objects.filter(user=request.user).first()
        hour = timezone.localtime().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        context = {"supplier": supplier, "greeting": greeting}
        context.update(_subscription_context(request.user))

        if supplier is None:
            return render(request, "home_manufacturer.html", context)

        # Reuses the same open/expiry filter as the RFQ inbox's "New" tab
        # (marketplace.services.open_requirements_for) instead of the old,
        # looser `Requirement.objects.filter(is_deleted=False)` this
        # dashboard used to count — that discrepancy meant the dashboard
        # and the RFQ inbox could disagree on how many RFQs were "open".
        new_rfqs = services.open_requirements_for(supplier).exclude(
            quote__supplier=supplier
        ).exclude(declines__supplier=supplier)

        pending_quotes = Quote.objects.filter(
            supplier=supplier, is_deleted=False, is_draft=False,
            is_selected=False, status__isnull=True,
        )
        quotes_awaiting_value = services.totals_by_currency(
            (q.requirement.quote_currency, q.get_breakdown()['total']) for q in pending_quotes.select_related('requirement')
        )

        active_orders = Order.objects.filter(supplier=supplier).exclude(status__in=['completed', 'cancelled'])

        quarter_start = services.quarter_start(timezone.now())
        quotes_this_quarter = Quote.objects.filter(supplier=supplier, is_deleted=False, created_at__gte=quarter_start)
        won_this_quarter = quotes_this_quarter.filter(is_selected=True).count()

        new_rfqs_table = list(new_rfqs.order_by('end_date')[:5])
        match_map = services.bulk_match_percent(new_rfqs_table, supplier)
        for requirement in new_rfqs_table:
            requirement.match_percent = match_map.get(requirement.pk)

        percent, checklist = supplier.profile_strength()

        context.update({
            "stat_new_rfqs": new_rfqs.count(),
            "stat_quotes_awaiting_count": pending_quotes.count(),
            "stat_quotes_awaiting_value": quotes_awaiting_value,
            "stat_active_orders": active_orders.count(),
            "stat_won_this_quarter": won_this_quarter,
            "stat_quotes_sent_this_quarter": quotes_this_quarter.count(),
            "new_rfqs_table": new_rfqs_table,
            "production_schedule": active_orders.order_by('ship_by_date')[:5],
            "profile_strength_percent": percent,
            "profile_strength_checklist": checklist,
        })
        return render(request, "home_manufacturer.html", context)


class AboutView(TemplateView):
    template_name = "about.html"


class HowItWorksView(TemplateView):
    template_name = "how_it_works.html"


class PricingView(TemplateView):
    template_name = "pricing.html"


class ContactView(TemplateView):
    template_name = "contact.html"


class PrivacyPolicyView(TemplateView):
    template_name = "privacy_policy.html"


class TermsOfServiceView(TemplateView):
    template_name = "terms_of_service.html"


class CareersView(TemplateView):
    template_name = "careers.html"


class SupplierCodeOfConductView(TemplateView):
    template_name = "supplier_code_of_conduct.html"


class CustomLoginView(LoginView):
    template_name = 'landing.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        # Default session behaviour (set globally) expires at browser close;
        # "Remember me" opts into a longer-lived session instead.
        if self.request.POST.get('remember_me'):
            self.request.session.set_expiry(60 * 60 * 24 * 14)  # 2 weeks
        else:
            self.request.session.set_expiry(0)
        return response

    def form_invalid(self, form):
        # Django's default LoginView re-renders the form in place (200) on
        # a bad login, which leaves the browser sitting on a POST response
        # — hitting refresh then pops a "Confirm Form Resubmission" prompt
        # (and can look like the page is just stuck). Redirect back to a
        # fresh GET instead and surface the error via the messages
        # framework, matching how register/logout feedback is shown.
        messages.error(self.request, "Incorrect email or password. Please try again.")
        return redirect('login')


class CustomLogoutView(LogoutView):
    next_page = 'home'

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "You've been logged out successfully.")
        return response
