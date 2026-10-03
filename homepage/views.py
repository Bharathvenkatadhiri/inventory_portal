import logging

from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import urlencode
from django.views.generic import View, TemplateView
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth import get_user_model, logout as auth_logout
from django.contrib.auth.views import LoginView, LogoutView, PasswordResetView
from django.contrib import messages
from django.utils import timezone
from core import audit, login_throttle, session_security
from marketplace import services
from marketplace.models import Requirement, Quote, Order
from accounts import team
from accounts.models import SubscriptionPlan
from plans import access, catalog, rfq_inbox
from accounts.views import plan_catalog, StaffRequiredMixin
from .feedback import staff_summary
from .forms import PortalFeedbackForm
from .models import PortalFeedback

logger = logging.getLogger(__name__)


@login_not_required
def custom_404_view(request, exception):
    return render(request, '404.html', status=404)


def _subscription_context(user):
    # The plan belongs to the company: it's held on the owner's account.
    subscription = SubscriptionPlan.objects.filter(user_profile=team.owner_user(user)).first()
    return {
        'subscription': subscription,
        'plan_catalog': plan_catalog(subscription, team.company_kind(user) or 'buyer'),
        'can_change_plan': team.can_manage_subscription(user),
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
        customer = team.buyer_profile(request.user)

        open_requirements = services.buyer_open_requirements(request.user)
        pending_quotes = services.buyer_pending_quotes(request.user)
        pending_quotes_value = services.totals_by_currency(
            (q.requirement.quote_currency, q.get_breakdown()['total']) for q in pending_quotes
        )
        active_orders = Order.objects.filter(customer=customer).exclude(status__in=['completed', 'cancelled'])

        quarter_start = services.quarter_start(timezone.now())
        orders_this_quarter = list(
            Order.objects.filter(customer=customer, created_at__gte=quarter_start).select_related('quote', 'supplier', 'requirement')
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
            "recent_requirements": Requirement.objects.filter(
                user_id__in=team.team_user_ids(request.user), is_deleted=False,
            ).order_by('-created_at')[:6],
            "action_items": services.buyer_action_items(request.user)[:3],
            "orders_in_progress": active_orders.order_by('ship_by_date')[:5],
        }
        context.update(_subscription_context(request.user))
        context["rfq_limit"], context["rfq_used"] = services.rfq_allowance(request.user)
        buyer = team.buyer_profile(request.user)
        context["usage"] = access.usage_rows(buyer) if buyer else []
        return render(request, "home_buyer.html", context)

    def _manufacturer_dashboard(self, request):
        supplier = team.supplier_profile(request.user)
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
        rfq_inbox.deliver(supplier)
        new_rfqs = rfq_inbox.received(services.open_requirements_for(supplier), supplier).exclude(
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
            "show_match": access.can(request.user, 'rfq.match_scores'),
            "recommended_rfqs": services.recommended_rfqs(
                supplier, advanced=access.has_feature(supplier, 'supplier_matching', catalog.ADVANCED),
            ) if access.can(request.user, 'rfq.recommendations') else None,
            "rfqs_waiting": rfq_inbox.waiting_count(supplier),
            "usage": access.usage_rows(supplier),
        })
        return render(request, "home_manufacturer.html", context)


class AboutView(TemplateView):
    template_name = "about.html"


class HowItWorksView(TemplateView):
    template_name = "how_it_works.html"


class PricingView(TemplateView):
    """The public pricing page: buyer and supplier plans, monthly or yearly,
    straight from plans.catalog so it can't drift from what's enforced."""
    template_name = "pricing.html"

    def get_context_data(self, **kwargs):
        from plans import catalog
        plans = [
            {'key': key, 'label': catalog.PLAN_LABELS[key],
             'monthly': catalog.price(key, catalog.MONTHLY), 'yearly': catalog.price(key, catalog.YEARLY)}
            for key in catalog.PLAN_ORDER
        ]
        sides = [
            {'key': side, 'title': title, 'rows': catalog.pricing_table(side),
             'cards': [{**plan, 'highlights': catalog.plan_highlights(side, plan['key'])} for plan in plans]}
            for side, title in ((catalog.BUYER, 'For Buyers'), (catalog.SUPPLIER, 'For Manufacturers'))
        ]
        return super().get_context_data(plans=plans, sides=sides, **kwargs)


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

    def post(self, request, *args, **kwargs):
        # Checked before the password is, so a locked-out attacker learns
        # nothing more — not even whether the email is registered.
        if login_throttle.is_locked_out(request, self._posted_email()):
            minutes = settings.LOGIN_FAILURE_WINDOW_SECONDS // 60
            messages.error(request, f"Too many failed login attempts. Please try again in {minutes} minutes or reset your password.")
            return redirect(self._login_url())
        return super().post(request, *args, **kwargs)

    def _posted_email(self):
        return (self.request.POST.get('username') or '').strip()

    def _login_url(self):
        # Keep a (validated) ?next= across a failed attempt, so someone
        # sent here from the admin or a deep link still lands there after
        # they get the password right.
        next_url = self.get_redirect_url()
        return f"{reverse('login')}?{urlencode({'next': next_url})}" if next_url else reverse('login')

    def form_valid(self, form):
        login_throttle.clear_failures(self._posted_email())
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
        email = self._posted_email()
        login_throttle.record_failure(self.request, email)
        hint_allowed = login_throttle.ip_failure_count(self.request) <= settings.LOGIN_UNREGISTERED_HINT_LIMIT
        if hint_allowed and email and not get_user_model().objects.filter(email__iexact=email).exists():
            # The "unregistered" tag tells landing.html to add a Register
            # link and keep the toast up long enough to click it.
            messages.error(
                self.request,
                "The email ID you entered is not registered.",
                extra_tags='unregistered',
            )
        else:
            messages.error(self.request, "Incorrect email or password. Please try again.")
        return redirect(self._login_url())


class ReauthView(View):
    """Confirm your password before a high-value action (see
    core/session_security.py). Wrong passwords count toward the same
    lockout as the login form, so this can't be used to guess a password
    on an already-open session either."""
    template_name = 'registration/reauth.html'

    def _next(self, request):
        return session_security.safe_next(request, request.POST.get('next') or request.GET.get('next'))

    def get(self, request):
        return render(request, self.template_name, {'next': self._next(request)})

    def post(self, request):
        next_url = self._next(request)
        email = request.user.email
        if login_throttle.is_locked_out(request, email):
            minutes = settings.LOGIN_FAILURE_WINDOW_SECONDS // 60
            messages.error(request, f"Too many failed attempts. Please try again in {minutes} minutes.")
            return render(request, self.template_name, {'next': next_url})
        if not request.user.check_password(request.POST.get('password', '')):
            login_throttle.record_failure(request, email)
            messages.error(request, "That password isn't right. Please try again.")
            return render(request, self.template_name, {'next': next_url})
        login_throttle.clear_failures(email)
        session_security.mark_recently_authenticated(request)
        return redirect(next_url)


class ThrottledPasswordResetView(PasswordResetView):
    """Django's reset view sends an email on every submission. Past the
    per-IP or per-email limit (login_throttle.password_reset_allowed) it
    still shows the usual "check your email" page, so the limit reveals
    nothing, but sends no email."""
    template_name = 'registration/password_reset_form.html'
    email_template_name = 'registration/password_reset_email.html'
    subject_template_name = 'registration/password_reset_subject.txt'

    def form_valid(self, form):
        if not login_throttle.password_reset_allowed(self.request, form.cleaned_data.get('email', '')):
            logger.warning("Password reset throttled for IP %s", self.request.META.get('REMOTE_ADDR', 'unknown'))
            return HttpResponseRedirect(self.get_success_url())
        return super().form_valid(form)


class CustomLogoutView(LogoutView):
    next_page = 'home'

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "You've been logged out successfully.")
        return response


class PortalFeedbackView(View):
    """A buyer or manufacturer rates MakeSetu. Submitting again
    updates their earlier feedback."""
    template_name = "feedback/portal_feedback.html"

    def get(self, request):
        existing = PortalFeedback.objects.filter(user=request.user).first()
        return render(request, self.template_name, {'form': PortalFeedbackForm(instance=existing), 'existing': existing})

    def post(self, request):
        existing = PortalFeedback.objects.filter(user=request.user).first()
        old_comment = existing.comment if existing else None
        form = PortalFeedbackForm(request.POST, instance=existing)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form, 'existing': existing})
        feedback = form.save(commit=False)
        feedback.user = request.user
        feedback.role = request.user.role
        if old_comment is not None and feedback.comment != old_comment:
            # Staff featured the old wording, not this one.
            feedback.is_featured = False
        feedback.save()
        logger.info("Portal feedback %s/5 saved by %s", feedback.rating, request.user)
        if request.POST.get('logout'):
            # Sent from the rating prompt on the Log out button.
            auth_logout(request)
            messages.success(request, "Thanks for your feedback! You've been logged out.")
            return redirect('home')
        messages.success(request, "Thanks for your feedback!")
        return redirect('portal-feedback')


class PortalFeedbackSummaryView(StaffRequiredMixin, View):
    def get(self, request):
        return render(request, "feedback/portal_feedback_summary.html", {'summary': staff_summary()})


class PortalFeedbackModerateView(StaffRequiredMixin, View):
    """Staff feature a review (pinned first on the public site) or hide it."""
    def get_reauth_return_url(self):
        return reverse('portal-feedback-summary') + '#reviews'

    def post(self, request, pk, action):
        feedback = get_object_or_404(PortalFeedback, pk=pk)
        if action == 'feature':
            feedback.is_featured = not feedback.is_featured
        elif action == 'hide':
            feedback.is_hidden = not feedback.is_hidden
        else:
            raise Http404
        feedback.save(update_fields=['is_featured', 'is_hidden'])
        state = feedback.is_featured if action == 'feature' else feedback.is_hidden
        audit.record(request, f"feedback.{action}", feedback, "on" if state else "off")
        return redirect(reverse('portal-feedback-summary') + '#reviews')
