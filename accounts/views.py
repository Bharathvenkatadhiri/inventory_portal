import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_not_required
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.exceptions import PermissionDenied, ValidationError
from .forms import (
    SupplierDetailsForm, updateSupplierDetailsForm, UserRegistrationForm, SelectCustomer, CustomerRegistrationForm,
    UpdateSubscription, updateCustomer, CompanyAboutForm, CompanyContactForm, CompanyCapacityForm, CompanyLUTForm,
    MachineForm, CertificationForm, CapabilityAddForm, MaterialAddForm,
)
from .models import (
    ManufacturerProfile, ConsumerProfile, SubscriptionPlan,
    Machine, ManufacturerPhoto, Certification, ManufacturingTech, PendingRegistration,
)
from .services import company_registration
from django.views.generic import (View, ListView, CreateView, UpdateView, DeleteView)
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.conf import settings
from django.apps import apps
from plans import access, catalog
from core.validators import IMAGE_EXTENSIONS, validate_upload
from core import audit, login_throttle, session_security
from . import otp, team

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)

logger = logging.getLogger(__name__)



class StaffRequiredMixin(UserPassesTestMixin):
    """Platform-admin screens (all customers, all suppliers, all
    subscriptions). Being logged in is not enough."""
    raise_exception = True

    def test_func(self):
        return self.request.user.is_staff

    def get_reauth_return_url(self):
        # The page to come back to after confirming the password. The POST
        # itself isn't replayed, so it's the page the action started from.
        return self.request.get_full_path()

    def dispatch(self, request, *args, **kwargs):
        # Every staff POST changes another account (deactivate, edit,
        # subscription, moderation): require a recently entered password,
        # so a hijacked staff session can't do it on its own.
        if request.method == 'POST' and request.user.is_authenticated and request.user.is_staff:
            reauth = session_security.require_recent_auth(request, self.get_reauth_return_url())
            if reauth is not None:
                return reauth
        return super().dispatch(request, *args, **kwargs)


def _staff_editing_someone_else(request, obj):
    return request.user.is_staff and obj.user_id != request.user.id


def plan_catalog(subscription, side=catalog.BUYER):
    """The plans for this company's side (buyer or supplier) from
    plans.catalog, annotated with which one it's on, which one (if any) is
    awaiting payment, and whether each is an upgrade or a downgrade — never
    fabricated pricing or features."""
    current = subscription.plan_type if subscription and subscription.plan_type in catalog.PLAN_LABELS else catalog.DEFAULT_PLAN
    current_cycle = subscription.billing_cycle if subscription else catalog.MONTHLY
    pending = subscription.pending_plan_type if subscription else ''
    current_rank = catalog.PLAN_ORDER.index(current)
    plans = []
    for rank, key in enumerate(catalog.PLAN_ORDER):
        limits = catalog.plan_entry(side, key)['limits']
        plans.append({
            'key': key,
            'label': catalog.PLAN_LABELS[key],
            'price_monthly': catalog.price(key, catalog.MONTHLY),
            'price_yearly': catalog.price(key, catalog.YEARLY),
            'highlights': [
                f"{catalog.describe_limit(catalog.USERS, limits.get(catalog.USERS))} users",
                *([f"{catalog.describe_limit(k, limits[k])} RFQs / month"] if (k := catalog.RFQS_PER_MONTH) in limits else []),
                *([f"{catalog.describe_limit(k, limits[k])} RFQs received / month"] if (k := catalog.RFQS_RECEIVED_PER_MONTH) in limits else []),
                *([f"{catalog.describe_limit(k, limits[k])} quotes / month"] if (k := catalog.QUOTES_PER_MONTH) in limits else []),
                f"{catalog.describe_limit(catalog.STORAGE_BYTES, limits.get(catalog.STORAGE_BYTES))} file storage",
            ],
            'is_current': rank == current_rank,
            'current_cycle': current_cycle if rank == current_rank else '',
            'is_pending': key == pending,
            'is_upgrade': rank > current_rank,
        })
    return plans


class CreateSupplier(SuccessMessageMixin, CreateView):
    model = ManufacturerProfile
    form_class = SupplierDetailsForm
    template_name = "register_supplier.html"
    success_url = '/'  # Redirects to home (the login page for anonymous users)
    success_message = "Your manufacturer account is all set. Log in to get started."

    def _pending_user(self):
        # request.user is still anonymous at this point in the registration
        # flow (the account created in `register` isn't logged in yet), so
        # look the user up from the session instead of using request.user.
        # Every persisted User is already email-verified (verify_email is
        # the only thing that creates one), so there's no unverified case
        # to filter out here.
        return User.objects.filter(
            pk=self.request.session.get('session_user_id'), role='manufacturer', manufacturerprofile__isnull=True,
            team_membership__isnull=True,
        ).first()

    def dispatch(self, request, *args, **kwargs):
        if self._pending_user() is None:
            if request.session.get('pending_registration_id'):
                return redirect('verify-email')
            messages.error(request, "Start by creating your account.")
            return redirect('register')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self._pending_user()
        return kwargs

    def get_initial(self):
        # The address the OTP step just verified; still editable, since the
        # company's contact email may differ from the login email.
        return {**super().get_initial(), 'email': self._pending_user().email}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session_user_id"] = self.request.session.get('session_user_id')
        context["session_username"] = self.request.session.get('session_username')
        context["session_first_name"] = self.request.session.get('session_first_name')
        context["session_last_name"] = self.request.session.get('session_last_name')
        context["session_email"] = self.request.session.get('session_email')
        # Re-shows the verified-company panel if this session already
        # verified a GSTIN (e.g. the form came back with an error).
        context["gst_verification"] = company_registration.session_verification(self.request)
        return context

    def form_invalid(self, form):
        return super().form_invalid(form)

    def form_valid(self, form):
        # The frontend never gets to assert "this company is GST verified":
        # the only source of truth is the GST verification this session ran
        # server-side (gst.views.verify_gstin). Legal name / address / GST
        # status come from it, never from posted form data.
        verification = company_registration.session_verification(self.request)
        if verification is None:
            form.add_error(None, "Please verify your company's GSTIN before submitting.")
            return self.form_invalid(form)
        conflict = company_registration.registration_conflict(self.request, verification.gstin, role='manufacturer')
        if conflict:
            form.add_error(None, conflict)
            return self.form_invalid(form)
        display_name = company_registration.display_name_from(self.request.POST.get('company_display_name'), verification)

        with transaction.atomic():
            company = company_registration.register_company(verification, display_name)
            profile = form.save(commit=False)
            # Never trust the posted hidden `user` field.
            profile.user = self._pending_user()
            profile.company = company
            profile.companyname = display_name[:40]
            profile.address = company.principal_address
            profile.city = company.city[:50]
            profile.state = company.state[:25]
            profile.country = "India"
            profile.save()
        self.object = profile

        company_registration.forget_session_verification(self.request)
        _end_profile_step(self.request)
        messages.success(self.request, self.success_message)
        return redirect(self.get_success_url())


def _start_profile_step(request, user):
    """Hands the new (not yet logged-in) user to the profile step via the session."""
    request.session['session_user_id'] = user.id
    request.session['session_username'] = user.username
    request.session['session_first_name'] = user.first_name
    request.session['session_last_name'] = user.last_name
    request.session['session_email'] = user.email


def _end_profile_step(request):
    for key in ('session_user_id', 'session_username', 'session_first_name', 'session_last_name', 'session_email'):
        request.session.pop(key, None)


@login_not_required
def register(request):
    if request.method == 'POST':
        email = request.POST.get('email')

        if ConsumerProfile.objects.filter(email=email).exists() \
                or ManufacturerProfile.objects.filter(email=email).exists():
            # An account under this email already completed registration.
            # Previously this fell through to a silent re-render with no
            # error — the user would click Register and nothing would
            # visibly happen. Confirming that outright is exactly what the
            # login form's own "not registered" hint is capped to stop an
            # attacker learning by the bucketful, so it's capped the same
            # way here — registration_hint_allowed shares login's per-IP
            # counter and limit, so probing through this form instead
            # spends the same budget a wrong login would.
            form = UserRegistrationForm(request.POST)
            if login_throttle.registration_hint_allowed(request):
                form.add_error('email', 'An account with this email already exists. Please log in instead.')
            else:
                form.add_error('email', "We couldn't register with these details. If you already have an account, sign in instead.")
            return render(request, 'register_first.html', {'form': form})

        existing_user = User.objects.filter(email=email).first()
        if existing_user is not None:
            # A verified User row exists but registration wasn't finished
            # (the supplier/customer profile step was abandoned) — resume
            # from there instead of erroring. Only with the right password:
            # without this check, typing someone else's email was enough to
            # take over their half-finished account.
            if not existing_user.check_password(request.POST.get('password1', '')):
                form = UserRegistrationForm(request.POST)
                if login_throttle.registration_hint_allowed(request):
                    form.add_error('email', 'An account with this email already exists. Enter its password to finish registering, or log in.')
                else:
                    form.add_error('email', "We couldn't register with these details. If you already have an account, sign in instead.")
                return render(request, 'register_first.html', {'form': form})
            if team.company(existing_user) is not None:
                # Already on a company account (e.g. joined through an
                # invitation): there's no profile step left to resume.
                form = UserRegistrationForm(request.POST)
                form.add_error('email', 'An account with this email already exists. Please log in instead.')
                return render(request, 'register_first.html', {'form': form})
            _start_profile_step(request, existing_user)
            return redirect('register-supplier' if existing_user.role == 'manufacturer' else 'register-customer')

        pending = PendingRegistration.objects.filter(email=email).first()
        if pending is not None:
            # Step one was already submitted for this email but never
            # verified — resume it rather than creating a second pending
            # signup. Same right-password check as the existing_user case.
            if not otp.password_matches(pending, request.POST.get('password1', '')):
                form = UserRegistrationForm(request.POST)
                if login_throttle.registration_hint_allowed(request):
                    form.add_error('email', 'An account with this email already exists. Enter its password to finish registering, or log in.')
                else:
                    form.add_error('email', "We couldn't register with these details. If you already have an account, sign in instead.")
                return render(request, 'register_first.html', {'form': form})
            request.session['pending_registration_id'] = pending.id
            if otp.resend_allowed(pending):
                otp.issue_and_send(pending)
            return redirect('verify-email')

        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            pending = otp.start_registration(form)
            request.session['pending_registration_id'] = pending.id
            return redirect('verify-email')
    else:
        form = UserRegistrationForm()
    return render(request, 'register_first.html', {'form': form})


@login_not_required
def verify_email(request):
    """Between account creation and the profile step: confirms the user
    controls the email address before a User row is ever created for them
    (accounts.otp holds the actual OTP logic and the pending signup data).
    Reached only via the session the register flow sets up — there's
    nothing to verify without it."""
    pending = PendingRegistration.objects.filter(pk=request.session.get('pending_registration_id')).first()
    if pending is None:
        messages.error(request, "Start by creating your account.")
        return redirect('register')

    if request.method == 'POST':
        if request.POST.get('action') == 'resend':
            if otp.resend_allowed(pending):
                otp.issue_and_send(pending)
                messages.success(request, f"A new code was sent to {pending.email}.")
            else:
                messages.error(request, "Too many codes requested. Please wait a few minutes and try again.")
            return redirect('verify-email')

        code = (request.POST.get('code') or '').strip()
        ok, reason = otp.verify(pending, code)
        if ok:
            user = otp.complete_registration(pending)
            SubscriptionPlan.objects.create(plan_type=catalog.DEFAULT_PLAN, price=0, user_profile=user)
            request.session.pop('pending_registration_id', None)
            _start_profile_step(request, user)
            messages.success(request, "Email verified.")
            return redirect('register-supplier' if user.role == 'manufacturer' else 'register-customer')
        errors = {
            'no_pending': "That code has expired. Request a new one below.",
            'expired': "That code has expired. Request a new one below.",
            'locked': "Too many incorrect attempts. Request a new code below.",
            'mismatch': "That code isn't right. Please try again.",
        }
        messages.error(request, errors.get(reason, "That code isn't right. Please try again."))
    elif not otp.has_pending_code(pending):
        # First visit after register(), or the earlier code has expired —
        # make sure there's always a live one to enter.
        otp.issue_and_send(pending)

    return render(request, 'registration/verify_email.html', {'email': pending.email})


class CreateCustomer(SuccessMessageMixin, CreateView):
    model = ConsumerProfile
    form_class = CustomerRegistrationForm
    success_url = '/'
    success_message = "Your buyer account is all set. Log in to get started."
    template_name = "register_customer.html"

    def _pending_user(self):
        # Every persisted User is already email-verified (verify_email is
        # the only thing that creates one), so there's no unverified case
        # to filter out here.
        return User.objects.filter(
            pk=self.request.session.get('session_user_id'), role='consumer', consumerprofile__isnull=True,
            team_membership__isnull=True,
        ).first()

    def dispatch(self, request, *args, **kwargs):
        if self._pending_user() is None:
            if request.session.get('pending_registration_id'):
                return redirect('verify-email')
            messages.error(request, "Start by creating your account.")
            return redirect('register')
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        # See CreateSupplier.get_initial.
        return {**super().get_initial(), 'email': self._pending_user().email}

    def form_valid(self, form):
        # Same rule as CreateSupplier.form_valid: the only proof of a
        # verified company is this session's server-side GST verification,
        # and the buyer's legal identity and address come from it.
        verification = company_registration.session_verification(self.request)
        if verification is None:
            form.add_error(None, "Please verify your company's GSTIN before submitting.")
            return self.form_invalid(form)
        conflict = company_registration.registration_conflict(self.request, verification.gstin, role='consumer')
        if conflict:
            form.add_error(None, conflict)
            return self.form_invalid(form)
        display_name = company_registration.display_name_from(self.request.POST.get('company_display_name'), verification)

        with transaction.atomic():
            company = company_registration.register_company(verification, display_name)
            profile = form.save(commit=False)
            profile.user = self._pending_user()
            profile.company = company
            profile.Name = display_name[:75]
            profile.Address = company.principal_address[:150]
            profile.city = company.city[:50]
            profile.state = company.state[:25]
            profile.country = "India"
            profile.save()
        self.object = profile

        company_registration.forget_session_verification(self.request)
        _end_profile_step(self.request)
        messages.success(self.request, self.success_message)
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session_user_id"] = self.request.session.get('session_user_id')
        context["session_username"] = self.request.session.get('session_username')
        context["session_first_name"] = self.request.session.get('session_first_name')
        context["session_last_name"] = self.request.session.get('session_last_name')
        context["session_email"] = self.request.session.get('session_email')
        context["gst_verification"] = company_registration.session_verification(self.request)
        context["title"] = 'New Customer'
        context["savebtn"] = 'Add Customer'
        return context



def ViewProfileDetails(request):
    # Settings page for both roles now — a manufacturer used to be sent
    # straight to CompanyProfileView here, which meant they had no page to
    # manage their own plan. That richer page (capabilities, machines,
    # photos...) still exists at its own 'company-profile' URL; this page
    # just adds Profile / Company summary / Plan & billing around it.
    context = {}
    customer = None
    supplier = None
    if request.user.role == 'manufacturer':
        supplier = team.supplier_profile(request.user)
        context['supplier'] = supplier
    else:
        customer = team.buyer_profile(request.user)
        if customer:
            context['customer'] = customer
    context['team_role'] = team.role_label(request.user)
    context['can_manage_company'] = team.can_manage_company(request.user)
    context['can_change_plan'] = team.can_manage_subscription(request.user)
    subscription = SubscriptionPlan.objects.filter(user_profile=team.owner_user(request.user)).first()
    context['subscription'] = subscription
    context['plan_catalog'] = plan_catalog(subscription, team.company_kind(request.user) or catalog.BUYER)
    context['usage'] = access.usage_rows(team.company(request.user))
    context['tab'] = request.GET.get('tab', 'profile')
    # Pre-fills the Contact us tab with the sender's own details.
    if supplier:
        context.update(contact_company=supplier.companyname or '', contact_phone=supplier.phone)
    elif customer:
        context.update(contact_company=customer.Name, contact_phone=customer.phone)
    context['contact_role'] = 'Manufacturer looking for RFQs' if request.user.role == 'manufacturer' else 'Buyer looking for parts'
    return render(request, 'profile.html', context)


class SubscriptionUpgradeView(LoginRequiredMixin, View):
    """Self-service plan change by the company's owner (buyer or
    manufacturer). Prices come from plans.catalog; the client only picks a
    plan and a billing cycle. Moving down applies immediately. Anything that
    costs more is recorded as a pending request: there is no payment
    integration yet, so staff apply it once payment is confirmed."""
    def post(self, request):
        plan_type = request.POST.get('plan_type')
        cycle = request.POST.get('billing_cycle') or catalog.MONTHLY
        next_url = request.POST.get('next')
        if not (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure())):
            next_url = reverse('profile') + '?tab=billing'
        if not team.can_manage_subscription(request.user):
            messages.error(request, "Only your company's owner can change the plan.")
            return redirect(next_url)
        if plan_type not in catalog.PLAN_LABELS or cycle not in dict(catalog.BILLING_CYCLES):
            messages.error(request, "Unknown plan selected.")
            return redirect(next_url)

        subscription = SubscriptionPlan.objects.filter(user_profile=request.user).first()
        if subscription is None:
            subscription = SubscriptionPlan(user_profile=request.user, plan_type=catalog.DEFAULT_PLAN, price=0)
        label = catalog.PLAN_LABELS[plan_type]
        current = subscription.plan_type if subscription.plan_type in catalog.PLAN_LABELS else catalog.DEFAULT_PLAN
        moving_down = catalog.PLAN_ORDER.index(plan_type) < catalog.PLAN_ORDER.index(current)
        new_price = catalog.price(plan_type, cycle)

        if new_price and not moving_down and (plan_type, cycle) != (current, subscription.billing_cycle):
            subscription.pending_plan_type = plan_type
            subscription.pending_billing_cycle = cycle
            subscription.pending_requested_at = timezone.now()
            subscription.save()
            logger.info("Subscription change to '%s' (%s) requested by %s (awaiting payment)", plan_type, cycle, request.user)
            messages.success(
                request,
                f"{label} ({cycle}) requested. We'll send you a payment link — your plan changes as soon as payment is confirmed.",
            )
            return redirect(next_url)

        subscription.plan_type = plan_type
        subscription.billing_cycle = cycle
        subscription.price = new_price
        subscription.is_active = True
        subscription.pending_plan_type = ''
        subscription.pending_billing_cycle = ''
        subscription.pending_requested_at = None
        subscription.save()
        logger.info("Subscription for %s changed to '%s'", request.user, plan_type)
        messages.success(request, f"You're now on the {label} plan.")
        return redirect(next_url)




class CustomerListView(StaffRequiredMixin, ListView):
    model = ConsumerProfile
    template_name = "customer/customer_list.html"
    paginate_by = 5

    def get_queryset(self):
        queryset = ConsumerProfile.objects.filter()
        email = self.request.GET.get('email')
        if email:
            queryset = queryset.filter(email__icontains=email)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context

class CustomerCreateView(StaffRequiredMixin, SuccessMessageMixin, CreateView):
    model = ConsumerProfile
    form_class = SelectCustomer
    success_url = '/accounts/customers'
    success_message = "Customer has been created successfully"
    template_name = "customer/edit_customer.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New Customer'
        context["savebtn"] = 'Add Customer'
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(self.request, 'customer.create', self.object)
        return response

class CustomerUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = ConsumerProfile
    form_class = updateCustomer
    success_message = "Company details updated."
    template_name = "customer/edit_customer.html"

    def get_object(self, queryset=None):
        # Staff can edit any buyer company; a buyer only their own (linked
        # from Settings → Company).
        obj = super().get_object(queryset)
        if not (self.request.user.is_staff or obj.user_id == self.request.user.id):
            raise PermissionDenied("You can only edit your own company details.")
        return obj

    def get_success_url(self):
        if self.request.user.is_staff and self.object.user_id != self.request.user.id:
            return reverse('customers-list')
        return reverse('profile') + '?tab=company'

    def post(self, request, *args, **kwargs):
        if _staff_editing_someone_else(request, self.get_object()):
            reauth = session_security.require_recent_auth(request, request.get_full_path())
            if reauth is not None:
                return reauth
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object.user_id != self.request.user.id:
            audit.record(self.request, 'customer.edit', self.object, "Changed: " + ", ".join(form.changed_data))
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit company details'
        context["savebtn"] = 'Save Changes'
        return context

class CustomerDeleteView(StaffRequiredMixin, View):
    template_name = "customer/delete_customer.html"
    success_message = "Customer Record has been deleted successfully"

    def get(self, request, pk):
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        return render(request, self.template_name, {'object' : customer})

    def post(self, request, pk):
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        User.objects.filter(id=customer.user_id).update(is_active=False)
        customer.is_deleted = True
        customer.save()
        audit.record(request, 'customer.deactivate', customer)
        messages.success(request, self.success_message)
        return redirect('customers-list')

class CustomeractivateView(StaffRequiredMixin, View):
    template_name = "customer/activate_customer.html"
    success_message = "Customer Record has been activated successfully"

    def get(self, request, pk):
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        return render(request, self.template_name, {'object' : customer})

    def post(self, request, pk):
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        User.objects.filter(id=customer.user_id).update(is_active=True)
        customer.is_deleted = False
        customer.save()
        audit.record(request, 'customer.activate', customer)
        messages.success(request, self.success_message)
        return redirect('customers-list')

class CustomerView(StaffRequiredMixin, View):
    def get(self, request, pk):
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        return render(request, 'customer/customer.html', {'customer' : customer})

class SubscriptionView(StaffRequiredMixin, ListView):
    model = SubscriptionPlan
    template_name = "subscription_list.html"
    queryset = SubscriptionPlan.objects.all()
    paginate_by = 5

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context

class SubscriptionDeleteView(StaffRequiredMixin, View):
    template_name = "delete_subscription.html"
    success_message = "Customer Record has been Deactivated successfully"

    def get(self, request, pk):
        subscription = get_object_or_404(SubscriptionPlan, pk=pk)
        return render(request, self.template_name, {'object' : subscription})

    def post(self, request, pk):
        subscription = get_object_or_404(SubscriptionPlan, pk=pk)
        subscription.is_active = False
        subscription.save()
        audit.record(request, 'subscription.deactivate', subscription)
        messages.success(request, self.success_message)
        return redirect('subscription-list')

class SubscriptionUpdateView(StaffRequiredMixin, SuccessMessageMixin, UpdateView):
    model = SubscriptionPlan
    form_class = UpdateSubscription
    success_url = '/accounts/subscription'
    success_message = "Customer details has been updated successfully"
    template_name = "edit_subscription.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Customer'
        context["savebtn"] = 'Save Changes'
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(self.request, 'subscription.edit', self.object, "Changed: " + ", ".join(form.changed_data))
        return response


class SupplierListView(StaffRequiredMixin, ListView):
    model = ManufacturerProfile
    template_name = "suppliers/suppliers_list.html"
    queryset = ManufacturerProfile.objects.filter()
    paginate_by = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


class SupplierDirectoryView(LoginRequiredMixin, ListView):
    """Buyer-facing "Find manufacturers" browse screen. Real filters over
    real fields only; ratings come from buyers' reviews of completed orders.

    Plans shape it from both sides (plans.catalog). The buyer's
    'supplier_directory': Basic is browse and keyword search over the first
    few pages; Full adds the filters and sorting. Each supplier's
    'directory_listing'/'search_visibility': Basic suppliers are listed but
    not in filtered results; Priority ones come first; 'featured_profile'
    ones also fill the Featured row."""
    model = ManufacturerProfile
    template_name = "suppliers/supplier_directory.html"
    paginate_by = 12

    SORTS = {
        'name': ['companyname'],
        'rating': [F('rating_avg').desc(nulls_last=True), '-rating_count', 'companyname'],
        'lead_time': [F('typical_lead_time_days').asc(nulls_last=True), 'companyname'],
    }
    FILTERS = ('process', 'certification', 'city', 'min_order')

    def full_directory(self):
        return access.can(self.request.user, 'directory.filter')

    def paginate_queryset(self, queryset, page_size):
        if not self.full_directory():
            page = self.request.GET.get('page', '1')
            if not page.isdigit() or int(page) > catalog.BASIC_DIRECTORY_PAGES:
                self.request.GET = self.request.GET.copy()
                self.request.GET['page'] = str(catalog.BASIC_DIRECTORY_PAGES)
                self.kwargs.pop('page', None)
        return super().paginate_queryset(queryset, page_size)

    def get_queryset(self):
        from marketplace.search import search_suppliers
        from marketplace.services import with_ratings
        queryset = ManufacturerProfile.objects.filter(is_deleted=False).select_related('company').prefetch_related(
            'capabilities', 'materials', 'certifications',
        )
        queryset = access.with_plan_levels(queryset, 'directory_listing', 'search_visibility')
        q = self.request.GET.get('q', '').strip()
        full = self.full_directory()
        process = self.request.GET.get('process', '') if full else ''
        certification = self.request.GET.get('certification', '') if full else ''
        city = self.request.GET.get('city', '') if full else ''
        min_order = self.request.GET.get('min_order', '') if full else ''
        # pk__in subqueries rather than joins, so no DISTINCT is needed
        # alongside the search and rating annotations.
        if process:
            queryset = queryset.filter(pk__in=ManufacturerProfile.objects.filter(capabilities__technology_type=process).values('pk'))
        if certification:
            queryset = queryset.filter(pk__in=Certification.objects.filter(name=certification).values('manufacturer'))
        if city:
            queryset = queryset.filter(city=city)
        if min_order:
            try:
                queryset = queryset.filter(minimum_order_qty__lte=int(min_order))
            except ValueError:
                pass
        if process or certification or city or min_order:
            # Filtered results list suppliers with a Full or Priority listing.
            queryset = queryset.filter(directory_listing_level__gte=catalog.STANDARD)
        queryset = with_ratings(queryset)
        sort = (self.request.GET.get('sort') if full else '') or ('relevance' if q else 'name')
        # Priority visibility comes first, whatever the sort.
        boost = F('search_visibility_level').desc()
        if q:
            queryset = search_suppliers(queryset, q)
            if sort == 'relevance':
                return queryset.order_by(boost, '-rank', 'companyname')
        return queryset.order_by(boost, *self.SORTS.get(sort, self.SORTS['name']))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        full = self.full_directory()
        context['full_directory'] = full
        context['basic_directory_pages'] = catalog.BASIC_DIRECTORY_PAGES
        context['q'] = self.request.GET.get('q', '').strip()
        context['sort'] = (self.request.GET.get('sort') if full else '') or ('relevance' if context['q'] else 'name')
        params = self.request.GET.copy()
        params.pop('page', None)
        context['querystring'] = params.urlencode()
        for name in self.FILTERS:
            context[name] = self.request.GET.get(name, '') if full else ''
        context['process_choices'] = ManufacturingTech.TECH_CHOICES
        context['certification_choices'] = Certification.objects.exclude(name='').order_by('name').values_list('name', flat=True).distinct()
        context['city_choices'] = ManufacturerProfile.objects.filter(is_deleted=False).exclude(city='').order_by('city').values_list('city', flat=True).distinct()
        for supplier in context['object_list']:
            supplier.is_priority = supplier.search_visibility_level >= catalog.ADVANCED
        if context.get('page_obj') is None or context['page_obj'].number == 1:
            featured = access.with_plan_levels(
                ManufacturerProfile.objects.filter(is_deleted=False), 'featured_profile',
            ).filter(featured_profile_level__gte=catalog.STANDARD)
            from marketplace.services import with_ratings
            context['featured'] = list(with_ratings(featured).order_by(F('rating_avg').desc(nulls_last=True), 'companyname')[:3])
        return context


class SupplierUpdateView(SuccessMessageMixin, UpdateView):
    model = ManufacturerProfile
    form_class = updateSupplierDetailsForm
    success_url = '/accounts/suppliers'
    success_message = "Supplier details has been updated successfully"
    template_name = "suppliers/edit_supplier.html"

    def get_object(self, queryset=None):
        # Fixed: this had no ownership check at all — any authenticated (or
        # even anonymous) user could edit any manufacturer's profile by
        # guessing its pk. Staff keeps the existing "edit any supplier"
        # capability (used from the admin suppliers-list); anyone else may
        # only edit their own profile.
        obj = super().get_object(queryset)
        if not (self.request.user.is_staff or obj.user_id == self.request.user.id):
            raise PermissionDenied("You can only edit your own manufacturer profile.")
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Supplier'
        context["savebtn"] = 'Save Changes'
        return context

    def post(self, request, *args, **kwargs):
        if _staff_editing_someone_else(request, self.get_object()):
            reauth = session_security.require_recent_auth(request, request.get_full_path())
            if reauth is not None:
                return reauth
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object.user_id != self.request.user.id:
            audit.record(self.request, 'supplier.edit', self.object, "Changed: " + ", ".join(form.changed_data))
        return response


class SupplierDeleteView(StaffRequiredMixin, View):
    template_name = "suppliers/delete_supplier.html"
    success_message = "Manufacturer has been deleted successfully"

    def get(self, request, pk):
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        return render(request, self.template_name, {'object': supplier})

    def post(self, request, pk):
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        # Deactivating a buyer always disabled their login; deactivating a
        # supplier only hid the profile, so a "deleted" manufacturer could
        # still sign in and keep quoting, messaging and updating orders.
        User.objects.filter(id=supplier.user_id).update(is_active=False)
        supplier.is_deleted = True
        supplier.save()
        audit.record(request, 'supplier.deactivate', supplier)
        messages.success(request, self.success_message)
        return redirect('suppliers-list')


class SupplieractivateView(StaffRequiredMixin, View):
    template_name = "suppliers/activate_supplier.html"
    success_message = "Supplier Record has been activated successfully"

    def get(self, request, pk):
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        return render(request, self.template_name, {'object': supplier})

    def post(self, request, pk):
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        User.objects.filter(id=supplier.user_id).update(is_active=True)
        supplier.is_deleted = False
        supplier.save()
        audit.record(request, 'supplier.activate', supplier)
        messages.success(request, self.success_message)
        return redirect('suppliers-list')


class SupplierView(View):
    def get(self, request, pk=''):
        from marketplace.services import rating_breakdown
        # A deactivated manufacturer is hidden from the directory; its
        # profile, certifications and photos shouldn't stay reachable by id
        # either. Staff still see it, to review before reactivating.
        profiles = ManufacturerProfile.objects.all() if request.user.is_staff else ManufacturerProfile.objects.filter(is_deleted=False)
        supplierobj = get_object_or_404(profiles, pk=pk)
        # What buyers see depends on the supplier's plan ('supplier_profile'):
        # Basic shows about, contact, capabilities and materials; Full adds
        # photos, machines and certifications; Advanced the cover image,
        # export (LUT) badge and reviews. Staff and the company itself see it all.
        own = team.supplier_profile(request.user) == supplierobj
        level = catalog.ADVANCED if (request.user.is_staff or own) else access.feature_level(supplierobj, 'supplier_profile')
        context = {
            'supplier': supplierobj,
            'rating': rating_breakdown(supplierobj),
            'profile_full': level >= catalog.STANDARD,
            'profile_advanced': level >= catalog.ADVANCED,
            'is_featured': access.has_feature(supplierobj, 'featured_profile'),
            'is_priority': access.has_feature(supplierobj, 'search_visibility', catalog.ADVANCED),
            'profile_level_label': catalog.LEVEL_WORDING['supplier_profile'].get(access.feature_level(supplierobj, 'supplier_profile'), ''),
            'is_own_profile': own,
            # Buyers reach this from Find manufacturers, so keep them in the
            # dashboard shell; staff come from the admin supplier list.
            'base_template': 'base.html' if request.user.is_staff else 'dashboard_base.html',
        }
        return render(request, 'suppliers/supplier.html', context)


class CompanyProfileView(LoginRequiredMixin, View):
    """Screen 5 of the manufacturer dashboard. Always looks up the profile
    from `request.user` — never a URL pk — which closes the SupplierUpdateView
    ownership-bug class by construction for every action on this page."""
    template_name = "suppliers/company_profile.html"

    def get(self, request):
        # Was a hard get_object_or_404 — any manufacturer-role user without
        # a ManufacturerProfile row yet (e.g. an account whose registration
        # never finished, or role flipped by hand in admin) got a real 404
        # instead of a helpful message. The dashboard already handles this
        # gracefully; this view now matches it instead of crashing.
        supplier = team.supplier_profile(request.user)
        if supplier is None:
            messages.info(request, "Your manufacturer profile isn't set up yet. Please contact support to finish setting up your account.")
            return redirect(reverse('home'))
        if not (team.allows(request.user, 'company.edit') or team.allows(request.user, 'capabilities.manage')):
            messages.info(request, "Only your company's owner or an admin can edit the company profile. This is how buyers see it.")
            return redirect(reverse('supplier', kwargs={'pk': supplier.pk}))
        from marketplace.services import rating_breakdown
        percent, checklist = supplier.profile_strength()
        context = {
            'supplier': supplier,
            'rating': rating_breakdown(supplier),
            'profile_strength_percent': percent,
            'profile_strength_checklist': checklist,
            'about_form': CompanyAboutForm(instance=supplier),
            'contact_form': CompanyContactForm(instance=supplier),
            'capacity_form': CompanyCapacityForm(instance=supplier),
            'lut_form': CompanyLUTForm(instance=supplier.company) if supplier.company else None,
            # Sales and operations manage capabilities, materials, machines and
            # certifications here; the rest of the profile is for owner/admins.
            'can_edit_profile': team.allows(request.user, 'company.edit'),
            'machine_form': MachineForm(),
            'certification_form': CertificationForm(),
            'capability_form': CapabilityAddForm(),
            'material_form': MaterialAddForm(),
        }
        return render(request, self.template_name, context)


class _CompanyProfileSubActionView(LoginRequiredMixin, View):
    """Shared helper: every sub-action below only ever touches the
    logged-in user's own company profile (and that profile's own child
    rows), never a pk taken from elsewhere in the URL. `action` is the role
    permission it needs (accounts.team.ROLE_PERMISSIONS): editing the
    profile is for the owner and admins; capabilities, materials, machines
    and certifications also for sales and operations."""
    action = 'company.edit'

    def get_supplier(self):
        supplier = team.supplier_profile(self.request.user)
        if supplier is None or not team.allows(self.request.user, self.action):
            raise Http404
        return supplier


class CompanyAboutUpdateView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        form = CompanyAboutForm(request.POST, request.FILES, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, "About section updated.")
        return redirect(reverse('company-profile'))


class CompanyContactUpdateView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        form = CompanyContactForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, "Primary contact updated.")
        return redirect(reverse('company-profile'))


class CompanyCapacityUpdateView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        form = CompanyCapacityForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, "Capacity settings updated.")
        return redirect(reverse('company-profile'))


class CompanyLUTUpdateView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        if supplier.company is None:
            messages.error(request, "Verify your company's GSTIN before adding a LUT.")
            return redirect(reverse('company-profile'))
        form = CompanyLUTForm(request.POST, instance=supplier.company)
        if form.is_valid():
            form.save()
            messages.success(request, "Export LUT saved. Exports are now quoted and invoiced without GST." if form.instance.has_valid_lut()
                             else "Export LUT details saved.")
        else:
            messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
        return redirect(reverse('company-profile'))


class CompanyCapabilityAddView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request):
        supplier = self.get_supplier()
        form = CapabilityAddForm(request.POST)
        if form.is_valid():
            supplier.capabilities.add(form.cleaned_data['capability'])
        return redirect(reverse('company-profile'))


class CompanyCapabilityRemoveView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request, pk):
        supplier = self.get_supplier()
        supplier.capabilities.remove(pk)
        return redirect(reverse('company-profile'))


class CompanyMaterialAddView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request):
        supplier = self.get_supplier()
        form = MaterialAddForm(request.POST)
        if form.is_valid():
            supplier.materials.add(form.cleaned_data['material'])
        return redirect(reverse('company-profile'))


class CompanyMaterialRemoveView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request, pk):
        supplier = self.get_supplier()
        supplier.materials.remove(pk)
        return redirect(reverse('company-profile'))


class CompanyMachineAddView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request):
        supplier = self.get_supplier()
        form = MachineForm(request.POST)
        if form.is_valid():
            machine = form.save(commit=False)
            machine.manufacturer = supplier
            machine.save()
        return redirect(reverse('company-profile'))


class CompanyMachineRemoveView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request, pk):
        supplier = self.get_supplier()
        get_object_or_404(Machine, pk=pk, manufacturer=supplier).delete()
        return redirect(reverse('company-profile'))


class CompanyPhotoUploadView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        image = request.FILES.get('image')
        if image:
            try:
                validate_upload(image, IMAGE_EXTENSIONS, verify_image=True)
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
                return redirect(reverse('company-profile'))
            ManufacturerPhoto.objects.create(manufacturer=supplier, image=image, caption=request.POST.get('caption', ''))
        return redirect(reverse('company-profile'))


class CompanyPhotoDeleteView(_CompanyProfileSubActionView):
    def post(self, request, pk):
        supplier = self.get_supplier()
        get_object_or_404(ManufacturerPhoto, pk=pk, manufacturer=supplier).delete()
        return redirect(reverse('company-profile'))


class CompanyCertificationUploadView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request):
        supplier = self.get_supplier()
        form = CertificationForm(request.POST, request.FILES)
        if form.is_valid():
            certification = form.save(commit=False)
            certification.manufacturer = supplier
            certification.save()
        return redirect(reverse('company-profile'))


class CompanyCertificationDeleteView(_CompanyProfileSubActionView):
    action = 'capabilities.manage'

    def post(self, request, pk):
        supplier = self.get_supplier()
        get_object_or_404(Certification, pk=pk, manufacturer=supplier).delete()
        return redirect(reverse('company-profile'))
