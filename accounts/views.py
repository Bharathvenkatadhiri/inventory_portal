import json
import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from .forms import (
    SupplierDetailsForm, updateSupplierDetailsForm, UserRegistrationForm, SelectCustomer, CustomerRegistrationForm,
    UpdateSubscription, updateCustomer, CompanyAboutForm, CompanyContactForm, CompanyCapacityForm,
    MachineForm, CertificationForm, CapabilityAddForm, MaterialAddForm,
)
from .models import (
    ManufacturerProfile, ConsumerProfile, SubscriptionPlan, Company,
    Machine, ManufacturerPhoto, Certification, ManufacturingTech,
)
from .services.gst_verification import verify_gstin
from .services.company_matching import company_names_match
from django.views.generic import (View, ListView, CreateView, UpdateView, DeleteView)
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.conf import settings
from django.apps import apps
from core.settings import subscription_plan_details

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)

logger = logging.getLogger(__name__)

GST_VERIFY_RATE_LIMIT = 5
GST_VERIFY_RATE_WINDOW_SECONDS = 60

# Same three tiers assigned at registration (see `register()` below) — the
# one place that lists/prices them, so the settings pages and the
# self-service upgrade view can't drift out of sync with each other.
PLAN_ORDER = ['basic', 'standard', 'enterprise']


class StaffRequiredMixin(UserPassesTestMixin):
    """Platform-admin screens (all customers, all suppliers, all
    subscriptions). Being logged in is not enough."""
    raise_exception = True

    def test_func(self):
        return self.request.user.is_staff


def plan_catalog(subscription):
    """Real plan tiers from settings.subscription_plan_details, annotated
    with which one the user is on, which one (if any) is awaiting payment,
    and whether each other tier is an upgrade or downgrade from the current
    one — never fabricated pricing or features."""
    plan_labels = dict(SubscriptionPlan.PLAN_CHOICES)
    current_plan_type = subscription.plan_type if subscription else 'basic'
    pending_plan_type = subscription.pending_plan_type if subscription else ''
    current_rank = PLAN_ORDER.index(current_plan_type) if current_plan_type in PLAN_ORDER else 0
    return [
        {
            'key': key,
            'label': plan_labels.get(key, key.title()),
            'price': subscription_plan_details[key]['price'],
            'rfq_limit': subscription_plan_details[key]['rfq_limit'],
            'is_current': rank == current_rank,
            'is_pending': key == pending_plan_type,
            'is_upgrade': rank > current_rank,
        }
        for rank, key in enumerate(PLAN_ORDER)
    ]


def _gst_verify_rate_limited(request):
    """Simple fixed-window throttle keyed by client IP, backed by Django's cache."""
    ident = request.META.get('REMOTE_ADDR', 'unknown')
    key = f"gst-verify-throttle:{ident}"
    count = cache.get(key, 0)
    if count >= GST_VERIFY_RATE_LIMIT:
        return True
    cache.set(key, count + 1, timeout=GST_VERIFY_RATE_WINDOW_SECONDS)
    return False


@login_not_required
@require_POST
def verify_gstin_view(request):
    """
    POST /api/companies/verify-gstin/
    Body: {"gstin": "...", "company_name": "..."}

    Verifies the GSTIN through the GST verification service, persists (or
    refreshes) the resulting Company row, and — only when the result is
    ACTIVE — records it in the session as this browser's verified company
    for the registration step to pick up. Nothing about "verified" is ever
    trusted from the request body on the registration submit; this endpoint
    is the only place that flips it on.
    """
    if _gst_verify_rate_limited(request):
        return JsonResponse(
            {"verified": False, "message": "Too many verification attempts. Please try again in a minute."},
            status=429,
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"verified": False, "message": "Invalid request."}, status=400)

    gstin = (payload.get('gstin') or '').strip()
    company_name = (payload.get('company_name') or '').strip()

    if not company_name:
        return JsonResponse({"verified": False, "message": "Company name is required."}, status=400)

    result = verify_gstin(gstin)
    if not result['success']:
        return JsonResponse(
            {"verified": False, "message": result['message']},
            status=result.get('http_status', 422),
        )

    normalized_gstin = result['gstin']
    existing = Company.objects.filter(gstin=normalized_gstin).first()
    if existing is not None and hasattr(existing, 'manufacturer_profile'):
        return JsonResponse(
            {"verified": False, "message": "This GSTIN is already registered with an existing account."},
            status=409,
        )

    gst_active = result['status'] == 'ACTIVE'
    name_match = company_names_match(company_name, result['legal_name'])

    if gst_active:
        verification_status = 'verified' if name_match else 'manual_review'
    elif result['status'] in ('SUSPENDED', 'UNKNOWN'):
        verification_status = 'manual_review'
    else:
        verification_status = 'failed'

    company, _created = Company.objects.update_or_create(
        gstin=normalized_gstin,
        defaults=dict(
            legal_name=result['legal_name'],
            trade_name=result['trade_name'],
            gst_status=result['status'],
            gst_verified=gst_active,
            gst_verified_at=timezone.now() if gst_active else None,
            registered_address=result['registered_address'],
            state=result['state'],
            city=result['city'],
            pincode=result['pincode'],
            cin=result['cin'],
            mca_status=result['mca_status'],
            entity_type=result['entity_type'],
            verification_status=verification_status,
        ),
    )

    if verification_status == 'verified':
        request.session['verified_company_id'] = company.id
    else:
        request.session.pop('verified_company_id', None)

    if verification_status == 'verified':
        message = "Company verified successfully."
    elif verification_status == 'manual_review' and gst_active:
        message = "GSTIN is active, but the company name doesn't closely match GST records. This will need manual review."
    elif verification_status == 'manual_review':
        message = "We couldn't confirm this GSTIN's status automatically. This will need manual review."
    else:
        message = "This GSTIN's registration is not currently active."

    return JsonResponse({
        "verified": verification_status == 'verified',
        "verification_status": verification_status,
        "name_match": name_match,
        "message": message,
        "company": {
            "legal_name": company.legal_name,
            "trade_name": company.trade_name,
            "gstin": company.gstin,
            "gst_status": company.gst_status,
            "registered_address": company.registered_address,
            "state": company.state,
            "city": company.city,
            "pincode": company.pincode,
            "entity_type": company.entity_type,
        },
    }, status=200 if gst_active else 422)


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
        return User.objects.filter(
            pk=self.request.session.get('session_user_id'), role='manufacturer', manufacturerprofile__isnull=True,
        ).first()

    def dispatch(self, request, *args, **kwargs):
        if self._pending_user() is None:
            messages.error(request, "Start by creating your account.")
            return redirect('register')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self._pending_user()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session_user_id"] = self.request.session.get('session_user_id')
        context["session_username"] = self.request.session.get('session_username')
        context["session_first_name"] = self.request.session.get('session_first_name')
        context["session_last_name"] = self.request.session.get('session_last_name')
        context["session_email"] = self.request.session.get('session_email')
        # Drives the pre-filled, read-only "verified company" panel if the
        # user already verified a GSTIN earlier in this session.
        context["verified_company"] = Company.objects.filter(
            pk=self.request.session.get('verified_company_id'),
            verification_status='verified',
        ).first()
        return context

    def form_invalid(self, form):
        return super().form_invalid(form)

    def form_valid(self, form):
        # The frontend never gets to assert "this company is GST verified" —
        # the only source of truth is a Company row this session verified
        # server-side (see verify_gstin_view). Legal name / address / GST
        # status are copied from that row, never from posted form data.
        company = Company.objects.filter(
            pk=self.request.session.get('verified_company_id'),
            verification_status='verified',
            gst_verified=True,
        ).first()
        if company is None:
            form.add_error(None, "Please verify your company's GSTIN before submitting.")
            return self.form_invalid(form)
        if hasattr(company, 'manufacturer_profile'):
            form.add_error(None, "This GSTIN is already registered with an existing account.")
            return self.form_invalid(form)

        profile = form.save(commit=False)
        # Never trust the posted hidden `user` field.
        profile.user = self._pending_user()
        profile.company = company
        profile.companyname = (company.trade_name or company.legal_name)[:40]
        profile.address = company.registered_address
        profile.city = company.city
        profile.state = company.state
        profile.country = "India"
        profile.save()
        self.object = profile

        # One verification -> one registration; drop the session marker so
        # it can't be replayed for a second account.
        self.request.session.pop('verified_company_id', None)
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
            # visibly happen.
            form = UserRegistrationForm(request.POST)
            form.add_error('email', 'An account with this email already exists. Please log in instead.')
            return render(request, 'register_first.html', {'form': form})

        existing_user = User.objects.filter(email=email).first()
        if existing_user is not None:
            # A User row exists but registration wasn't finished (the
            # supplier/customer profile step was abandoned) — resume from
            # there instead of erroring. Only with the right password:
            # without this check, typing someone else's email was enough to
            # take over their half-finished account.
            if not existing_user.check_password(request.POST.get('password1', '')):
                form = UserRegistrationForm(request.POST)
                form.add_error('email', 'An account with this email already exists. Enter its password to finish registering, or log in.')
                return render(request, 'register_first.html', {'form': form})
            _start_profile_step(request, existing_user)
            return redirect('register-supplier' if existing_user.role == 'manufacturer' else 'register-customer')

        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            _start_profile_step(request, user)
            SubscriptionPlan.objects.create(
                plan_type='basic',
                price=subscription_plan_details['basic']['price'],
                rfq_limit=subscription_plan_details['basic']['rfq_limit'],
                user_profile=user,
            )
            return redirect('register-supplier' if user.role == 'manufacturer' else 'register-customer')
    else:
        form = UserRegistrationForm()
    return render(request, 'register_first.html', {'form': form})


class CreateCustomer(SuccessMessageMixin, CreateView):
    model = ConsumerProfile
    form_class = CustomerRegistrationForm
    success_url = '/'
    success_message = "Your buyer account is all set. Log in to get started."
    template_name = "register_customer.html"

    def _pending_user(self):
        return User.objects.filter(
            pk=self.request.session.get('session_user_id'), role='consumer', consumerprofile__isnull=True,
        ).first()

    def dispatch(self, request, *args, **kwargs):
        if self._pending_user() is None:
            messages.error(request, "Start by creating your account.")
            return redirect('register')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.user = self._pending_user()
        response = super().form_valid(form)
        _end_profile_step(self.request)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session_user_id"] = self.request.session.get('session_user_id')
        context["session_username"] = self.request.session.get('session_username')
        context["session_first_name"] = self.request.session.get('session_first_name')
        context["session_last_name"] = self.request.session.get('session_last_name')
        context["session_email"] = self.request.session.get('session_email')
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
        supplier = ManufacturerProfile.objects.filter(user=request.user).first()
        context['supplier'] = supplier
    else:
        customer = ConsumerProfile.objects.filter(user=request.user.id).first()
        if customer:
            context['customer'] = customer
    subscription = SubscriptionPlan.objects.filter(user_profile=request.user).first()
    context['subscription'] = subscription
    context['plan_catalog'] = plan_catalog(subscription)
    context['tab'] = request.GET.get('tab', 'profile')
    return render(request, 'profile.html', context)


class SubscriptionUpgradeView(LoginRequiredMixin, View):
    """Self-service plan change for the logged-in user (buyer or
    manufacturer). Price and RFQ limit are always looked up server-side
    from settings.subscription_plan_details; the client only picks a plan
    key. Moving to a cheaper plan applies immediately. Moving to a more
    expensive one is recorded as a pending request: there is no payment
    integration yet, so staff apply it once payment is confirmed."""
    def post(self, request):
        plan_type = request.POST.get('plan_type')
        details = subscription_plan_details.get(plan_type)
        next_url = request.POST.get('next')
        if not (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure())):
            next_url = reverse('profile') + '?tab=billing'
        if not details:
            messages.error(request, "Unknown plan selected.")
            return redirect(next_url)

        subscription = SubscriptionPlan.objects.filter(user_profile=request.user).first()
        if subscription is None:
            basic = subscription_plan_details['basic']
            subscription = SubscriptionPlan(user_profile=request.user, plan_type='basic', price=basic['price'], rfq_limit=basic['rfq_limit'])
        label = dict(SubscriptionPlan.PLAN_CHOICES)[plan_type]

        if details['price'] > (subscription.price or 0):
            subscription.pending_plan_type = plan_type
            subscription.pending_requested_at = timezone.now()
            subscription.save()
            logger.info("Subscription upgrade to '%s' requested by %s (awaiting payment)", plan_type, request.user)
            messages.success(
                request,
                f"Upgrade to {label} requested. We'll send you a payment link — your plan changes as soon as payment is confirmed.",
            )
            return redirect(next_url)

        subscription.plan_type = plan_type
        subscription.price = details['price']
        subscription.rfq_limit = details['rfq_limit']
        subscription.is_active = True
        subscription.pending_plan_type = ''
        subscription.pending_requested_at = None
        subscription.save()
        logger.info("Subscription for %s changed to '%s'", request.user, plan_type)
        messages.success(request, f"You're now on the {label} plan.")
        return redirect(next_url)

        subscription = SubscriptionPlan.objects.filter(user_profile=request.user).first()
        if subscription is None:
            subscription = SubscriptionPlan(user_profile=request.user)
        subscription.plan_type = plan_type
        subscription.price = details['price']
        subscription.rfq_limit = details['rfq_limit']
        subscription.is_active = True
        subscription.save()
        logger.info("Subscription for %s changed to '%s'", request.user, plan_type)
        messages.success(request, f"You're now on the {subscription.get_plan_type_display()} plan.")
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
        user_id = ConsumerProfile.objects.filter(pk=pk).values('user_id').last()
        User.objects.filter(id=user_id['user_id']).update(is_active=False)
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        customer.is_deleted = True
        customer.save()
        messages.success(request, self.success_message)
        return redirect('customers-list')

class CustomeractivateView(StaffRequiredMixin, View):
    template_name = "customer/activate_customer.html"
    success_message = "Customer Record has been activated successfully"

    def get(self, request, pk):
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        return render(request, self.template_name, {'object' : customer})

    def post(self, request, pk):
        user_id = ConsumerProfile.objects.filter(pk=pk).values('user_id').last()
        User.objects.filter(id=user_id['user_id']).update(is_active=True)
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        customer.is_deleted = False
        customer.save()
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
    real fields only — no fabricated ratings/review counts, since the data
    model doesn't have them yet."""
    model = ManufacturerProfile
    template_name = "suppliers/supplier_directory.html"
    paginate_by = 12

    def get_queryset(self):
        queryset = ManufacturerProfile.objects.filter(is_deleted=False).select_related('company').prefetch_related(
            'capabilities', 'materials', 'certifications',
        )
        process = self.request.GET.get('process', '')
        certification = self.request.GET.get('certification', '')
        city = self.request.GET.get('city', '')
        min_order = self.request.GET.get('min_order', '')
        if process:
            queryset = queryset.filter(capabilities__technology_type=process)
        if certification:
            queryset = queryset.filter(certifications__name=certification)
        if city:
            queryset = queryset.filter(city=city)
        if min_order:
            try:
                queryset = queryset.filter(minimum_order_qty__lte=int(min_order))
            except ValueError:
                pass
        return queryset.distinct().order_by('companyname')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['process'] = self.request.GET.get('process', '')
        context['certification'] = self.request.GET.get('certification', '')
        context['city'] = self.request.GET.get('city', '')
        context['min_order'] = self.request.GET.get('min_order', '')
        context['process_choices'] = ManufacturingTech.TECH_CHOICES
        context['certification_choices'] = Certification.objects.exclude(name='').order_by('name').values_list('name', flat=True).distinct()
        context['city_choices'] = ManufacturerProfile.objects.filter(is_deleted=False).exclude(city='').order_by('city').values_list('city', flat=True).distinct()
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


class SupplierDeleteView(StaffRequiredMixin, View):
    template_name = "suppliers/delete_supplier.html"
    success_message = "Manufacturer has been deleted successfully"

    def get(self, request, pk):
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        return render(request, self.template_name, {'object': supplier})

    def post(self, request, pk):
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        supplier.is_deleted = True
        supplier.save()
        messages.success(request, self.success_message)
        return redirect('suppliers-list')


class SupplieractivateView(StaffRequiredMixin, View):
    template_name = "suppliers/activate_supplier.html"
    success_message = "Supplier Record has been activated successfully"

    def get(self, request, pk):
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        return render(request, self.template_name, {'object': supplier})

    def post(self, request, pk):
        user_id = ManufacturerProfile.objects.filter(pk=pk).values('user_id').last()
        User.objects.filter(id=user_id['user_id']).update(is_active=True)
        supplier = get_object_or_404(ManufacturerProfile, pk=pk)
        supplier.is_deleted = False
        supplier.save()
        messages.success(request, self.success_message)
        return redirect('suppliers-list')


class SupplierView(View):
    def get(self, request, pk=''):
        supplierobj = get_object_or_404(ManufacturerProfile, pk=pk)
        context = {
            'supplier': supplierobj,
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
        supplier = ManufacturerProfile.objects.filter(user=request.user).first()
        if supplier is None:
            messages.info(request, "Your manufacturer profile isn't set up yet. Please contact support to finish setting up your account.")
            return redirect(reverse('home'))
        percent, checklist = supplier.profile_strength()
        context = {
            'supplier': supplier,
            'profile_strength_percent': percent,
            'profile_strength_checklist': checklist,
            'about_form': CompanyAboutForm(instance=supplier),
            'contact_form': CompanyContactForm(instance=supplier),
            'capacity_form': CompanyCapacityForm(instance=supplier),
            'machine_form': MachineForm(),
            'certification_form': CertificationForm(),
            'capability_form': CapabilityAddForm(),
            'material_form': MaterialAddForm(),
        }
        return render(request, self.template_name, context)


class _CompanyProfileSubActionView(LoginRequiredMixin, View):
    """Shared helper: every sub-action below only ever touches the
    logged-in manufacturer's own profile (and that profile's own child
    rows), never a pk taken from elsewhere in the URL."""

    def get_supplier(self):
        return get_object_or_404(ManufacturerProfile, user=self.request.user)


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


class CompanyCapabilityAddView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        form = CapabilityAddForm(request.POST)
        if form.is_valid():
            supplier.capabilities.add(form.cleaned_data['capability'])
        return redirect(reverse('company-profile'))


class CompanyCapabilityRemoveView(_CompanyProfileSubActionView):
    def post(self, request, pk):
        supplier = self.get_supplier()
        supplier.capabilities.remove(pk)
        return redirect(reverse('company-profile'))


class CompanyMaterialAddView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        form = MaterialAddForm(request.POST)
        if form.is_valid():
            supplier.materials.add(form.cleaned_data['material'])
        return redirect(reverse('company-profile'))


class CompanyMaterialRemoveView(_CompanyProfileSubActionView):
    def post(self, request, pk):
        supplier = self.get_supplier()
        supplier.materials.remove(pk)
        return redirect(reverse('company-profile'))


class CompanyMachineAddView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        form = MachineForm(request.POST)
        if form.is_valid():
            machine = form.save(commit=False)
            machine.manufacturer = supplier
            machine.save()
        return redirect(reverse('company-profile'))


class CompanyMachineRemoveView(_CompanyProfileSubActionView):
    def post(self, request, pk):
        supplier = self.get_supplier()
        get_object_or_404(Machine, pk=pk, manufacturer=supplier).delete()
        return redirect(reverse('company-profile'))


class CompanyPhotoUploadView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        image = request.FILES.get('image')
        if image:
            ManufacturerPhoto.objects.create(manufacturer=supplier, image=image, caption=request.POST.get('caption', ''))
        return redirect(reverse('company-profile'))


class CompanyPhotoDeleteView(_CompanyProfileSubActionView):
    def post(self, request, pk):
        supplier = self.get_supplier()
        get_object_or_404(ManufacturerPhoto, pk=pk, manufacturer=supplier).delete()
        return redirect(reverse('company-profile'))


class CompanyCertificationUploadView(_CompanyProfileSubActionView):
    def post(self, request):
        supplier = self.get_supplier()
        form = CertificationForm(request.POST, request.FILES)
        if form.is_valid():
            certification = form.save(commit=False)
            certification.manufacturer = supplier
            certification.save()
        return redirect(reverse('company-profile'))


class CompanyCertificationDeleteView(_CompanyProfileSubActionView):
    def post(self, request, pk):
        supplier = self.get_supplier()
        get_object_or_404(Certification, pk=pk, manufacturer=supplier).delete()
        return redirect(reverse('company-profile'))
