from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_not_required
from .forms import SupplierDetailsForm, updateSupplierDetailsForm, UserRegistrationForm, SelectCustomer, UpdateSubscription, updateCustomer
from .models import ManufacturerProfile, ConsumerProfile, SubscriptionPlan
from django.views.generic import (View, ListView, CreateView, UpdateView, DeleteView)
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.conf import settings
from django.apps import apps
from core.settings import subscription_plan_details

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)


class CreateSupplier(SuccessMessageMixin, CreateView):
    model = ManufacturerProfile
    form_class = SupplierDetailsForm
    template_name = "register_supplier.html"
    success_url = '/#login'  # Redirects to home page after submitting the form
    success_message = "Manufacturer account has been created successfully"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the user to the form
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session_user_id"] = self.request.session.get('session_user_id')
        context["session_username"] = self.request.session.get('session_username')
        context["session_first_name"] = self.request.session.get('session_first_name')
        context["session_last_name"] = self.request.session.get('session_last_name')
        context["session_email"] = self.request.session.get('session_email')
        context["session_is_staff"] = self.request.session.get('session_is_staff')
        return context
    def form_invalid(self, form):
        return super().form_invalid(form)


@login_not_required
def register(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        is_staff = request.POST.get('is_staff')
        if ConsumerProfile.objects.filter(email=email).exists() \
            or ManufacturerProfile.objects.filter(email=email).exists():
                form = UserRegistrationForm(request.POST)
        else:
            if User.objects.filter(email=email).exists():
                if is_staff=='1':
                    return redirect('register-supplier')
                else:
                    return redirect('register-customer')
            else:
                form = UserRegistrationForm(request.POST)
                if form.is_valid():
                    user = form.save()
                    request.session['session_user_id'] = user.id
                    request.session['session_username'] = user.username
                    request.session['session_first_name'] = user.first_name
                    request.session['session_last_name'] = user.last_name
                    request.session['session_is_staff'] = user.is_staff
                    request.session['session_email'] = user.email
                    subscription_plan = SubscriptionPlan(plan_type='basic',
                        price=subscription_plan_details['basic']['price'],
                        rfq_limit = subscription_plan_details['basic']['rfq_limit'],
                        user_profile_id = email)
                    subscription_plan.save()
                    if user.is_staff:
                        return redirect('register-supplier')
                    else:
                        return redirect('register-customer')
    else:
        form = UserRegistrationForm()
    return render(request, 'register_first.html', {'form': form})


class CreateCustomer(SuccessMessageMixin, CreateView):
    model = ConsumerProfile
    form_class = SelectCustomer
    success_url = '/#login'
    success_message = "Customer has been created successfully"
    template_name = "register_customer.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session_user_id"] = self.request.session.get('session_user_id')
        context["session_username"] = self.request.session.get('session_username')
        context["session_first_name"] = self.request.session.get('session_first_name')
        context["session_last_name"] = self.request.session.get('session_last_name')
        context["session_email"] = self.request.session.get('session_email')
        context["session_is_staff"] = self.request.session.get('session_is_staff')
        context["title"] = 'New Customer'
        context["savebtn"] = 'Add Customer'
        return context



def ViewProfileDetails(request):
    context = {}
    if request.user.is_staff:
        supplier = ManufacturerProfile.objects.filter(user=request.user).first()
        if supplier:
            context['supplier'] = supplier
    else:
        customer = ConsumerProfile.objects.filter(user=request.user.id).first()
        if customer:
            context['customer'] = customer
    return render(request, 'profile.html', context)




class CustomerListView(ListView):
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

class CustomerCreateView(SuccessMessageMixin, CreateView):
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

class CustomerUpdateView(SuccessMessageMixin, UpdateView):
    model = ConsumerProfile
    form_class = updateCustomer
    success_url = '/accounts/customers'
    success_message = "Customer details has been updated successfully"
    template_name = "customer/edit_customer.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Customer'
        context["savebtn"] = 'Save Changes'
        return context

class CustomerDeleteView(View):
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

class CustomeractivateView(View):
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

class CustomerView(View):
    def get(self, request, pk):
        customer = get_object_or_404(ConsumerProfile, pk=pk)
        return render(request, 'customer/customer.html', {'customer' : customer})

class SubscriptionView(ListView):
    model = SubscriptionPlan
    template_name = "subscription_list.html"
    queryset = SubscriptionPlan.objects.all()
    paginate_by = 5

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context

class SubscriptionDeleteView(View):
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

class SubscriptionUpdateView(SuccessMessageMixin, UpdateView):
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


class SupplierListView(ListView):
    model = ManufacturerProfile
    template_name = "suppliers/suppliers_list.html"
    queryset = ManufacturerProfile.objects.filter()
    paginate_by = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


class SupplierUpdateView(SuccessMessageMixin, UpdateView):
    model = ManufacturerProfile
    form_class = updateSupplierDetailsForm
    success_url = '/accounts/suppliers'
    success_message = "Supplier details has been updated successfully"
    template_name = "suppliers/edit_supplier.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Supplier'
        context["savebtn"] = 'Save Changes'
        return context


class SupplierDeleteView(View):
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


class SupplieractivateView(View):
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
