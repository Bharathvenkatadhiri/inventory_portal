from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.generic import (
    View,
    ListView,
    CreateView,
    UpdateView,
)
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Q
from django.forms import formset_factory
from django.utils import timezone
from django.conf import settings
from django.apps import apps

from accounts.models import ManufacturerProfile, ConsumerProfile
from utils import utils

from .models import Requirement, RequirementPart, Quote, Order
from .forms import SelectRequirement, RequirementPartForm, SelectQuote

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)


class RequirementListStatusView(LoginRequiredMixin, ListView):
    model = Requirement
    template_name = "requirement/requirement_list.html"
    paginate_by = 10

    def get_queryset(self):
        status = self.kwargs.get('status')
        user = self.request.user
        if user.is_staff:
            supplier = ManufacturerProfile.objects.filter(user=user).first()
            requirement = Requirement.objects.filter(
                is_deleted=False, status=status, quote__supplier=supplier, quote__is_selected=True
            )
        else:
            requirement = Requirement.objects.filter(is_deleted=False, status=status, user=user)
        return requirement

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context


class RequirementListView(LoginRequiredMixin, ListView):
    model = Requirement
    template_name = "requirement/requirement_list.html"
    paginate_by = 10

    def get_queryset(self):
        user = self.request.user
        sort = self.request.GET.get('sort', '')
        industry_id = self.request.GET.get('industry', '')
        if user.is_staff:
            # RFQs that don't yet have a selected quote — still open for quoting.
            queryset = Requirement.objects.filter(
                end_date__gte=timezone.now(), is_deleted=False
            ).exclude(quote__is_selected=True).distinct()
        else:
            queryset = Requirement.objects.filter(user=user, is_deleted=False)
        if industry_id:
            queryset = queryset.filter(industry_id=industry_id)
        if sort == 'date_asc':
            queryset = queryset.order_by('end_date')
        elif sort == 'date_desc':
            queryset = queryset.order_by('-end_date')
        elif sort == 'parts_asc':
            queryset = queryset.order_by('parts')
        elif sort == 'parts_desc':
            queryset = queryset.order_by('-parts')
        if sort == 'cr_date_asc':
            queryset = queryset.order_by('created_at')
        elif sort == 'cr_date_desc':
            queryset = queryset.order_by('-created_at')
        else:
            queryset = queryset.order_by('-pk')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sort'] = self.request.GET.get('sort', '')
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context


class RequirementCreateView(SuccessMessageMixin, CreateView):
    model = Requirement
    form_class = SelectRequirement
    template_name = "requirement/edit_requirement.html"
    success_url = '/marketplace/requirement'
    success_message = "RFQ has been created successfully"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New RFQ'
        context["savebtn"] = 'Add RFQ'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        PartFormSet = formset_factory(RequirementPartForm, extra=1)
        context["formset"] = PartFormSet()
        return context

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if form.is_valid():
            requirement = form.save()

            num_parts = int(request.POST.get('parts', 0))
            PartFormSet = formset_factory(RequirementPartForm, extra=num_parts)
            parts_formset = PartFormSet(request.POST, request.FILES)
            if parts_formset.is_valid():
                for part_form in parts_formset:
                    if part_form.cleaned_data:
                        part = part_form.save(commit=False)
                        part.requirement = requirement
                        part.save()
            return redirect(self.success_url)
        else:
            return self.form_invalid(form)


class RequirementUpdateView(SuccessMessageMixin, UpdateView):
    model = Requirement
    form_class = SelectRequirement
    success_url = '/marketplace/requirement'
    success_message = "RFQ details has been updated successfully"
    template_name = "requirement/edit_requirement.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Requirement'
        context["savebtn"] = 'Save Changes'
        context["delbtn"] = 'Delete Requirement'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context


class RequirementDeleteView(View):
    template_name = "requirement/delete_requirement.html"
    success_message = "Requirement Record has been deleted successfully"

    def get(self, request, pk):
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'
        requirement = get_object_or_404(Requirement, pk=pk)
        return render(request, self.template_name, {'object': requirement, 'base_template': base_template})

    def post(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk)
        requirement.is_deleted = True
        requirement.save()
        messages.success(request, self.success_message)
        return redirect('requirement-list')


class RequirementView(View):
    def get(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk)
        requirement_parts = RequirementPart.objects.filter(requirement=requirement).all()
        quote = Quote.objects.filter(requirement=requirement)
        btn_class = 'ghost-blue'
        requirement.demand_buttons = utils.demand_buttons(requirement, request.user.is_staff)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'
        return render(request, 'requirement/requirement.html', {
            'demand': requirement,
            'quotes': quote,
            'demanddetails': requirement_parts,
            'btn_class': btn_class,
            'base_template': base_template,
        })


class QuoteListView(ListView):
    model = Quote
    template_name = "quote/quote_list.html"
    paginate_by = 10

    def get_queryset(self):
        user = self.request.user.id
        supplier = ManufacturerProfile.objects.filter(user=user).first()
        queryset = Quote.objects.filter(is_deleted=False, supplier=supplier)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context


class QuoteCreateView(SuccessMessageMixin, CreateView):
    model = Quote
    form_class = SelectQuote
    success_url = '/marketplace/quote'
    success_message = "Quotation has been created successfully"
    template_name = "quote/edit_quote.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New Quote'
        context["savebtn"] = 'Add Quote'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        context["demand"] = Requirement.objects.filter(pk=self.kwargs.get('pk')).first()
        return context

    def post(self, request, *args, **kwargs):
        supplier_id = request.POST.get('supplier')
        quote_price = request.POST.get('quote_price')
        note = request.POST.get('note')
        pk = self.kwargs.get('pk')
        requirement = Requirement.objects.get(pk=pk)
        supplier_details = ManufacturerProfile.objects.get(user=supplier_id)
        quote = Quote(
            requirement=requirement,
            supplier=supplier_details,
            quote_price=quote_price,
            note=note
        )
        quote.save()
        messages.success(request, self.success_message)
        return redirect(self.success_url)


class QuoteUpdateView(SuccessMessageMixin, UpdateView):
    model = Quote
    form_class = SelectQuote
    success_url = '/marketplace/quote'
    success_message = "Quotation details has been updated successfully"
    template_name = "quote/edit_quote.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Quote'
        context["savebtn"] = 'Save Changes'
        context["delbtn"] = 'Delete Quote'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context


class QuoteDeleteView(View):
    template_name = "quote/delete_quote.html"
    success_message = "Quotation has been deleted successfully"

    def get(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'
        return render(request, self.template_name, {'object': quote, 'base_template': base_template})

    def post(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        quote.is_deleted = True
        quote.save()
        messages.success(request, self.success_message)
        return redirect('quote-list')


class QuoteView(View):
    def get(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'
        return render(request, 'quote/quote.html', {'quote': quote, 'base_template': base_template})


class QuoteStatusUpdateView(View):
    def get(self, request, pk, status):
        quote = get_object_or_404(Quote, pk=pk)
        requirement = Requirement.objects.get(pk=quote.requirement.id)
        if status == 'Approved':
            quote.status = 'Approved'
            quote.is_selected = True
            requirement.status = "Approved"
            requirement.save()
            other_quotes = Quote.objects.filter(requirement=quote.requirement, status__isnull=True).exclude(pk=quote.pk)
            for reject_quote in other_quotes:
                reject_quote.status = 'Rejected'
                reject_quote.save()
        elif status == 'Rejected':
            quote.status = 'Rejected'
        quote.save()
        return redirect(reverse('requirement', kwargs={'pk': requirement.id}))


class RequirementStatusUpdateView(View):
    def get(self, request, pk, status):
        requirement = get_object_or_404(Requirement, pk=pk)
        if status == 'Production' and requirement.status == 'Approved':
            requirement.status = 'Production'
            requirement.save()
        if status == 'Completed' and requirement.status == 'Production':
            requirement.status = 'Completed'
            requirement.save()
            if not Order.objects.filter(requirement=requirement):
                quote = Quote.objects.filter(requirement=requirement, is_selected=True).first()
                if quote:
                    customer = get_object_or_404(ConsumerProfile, user=requirement.user)
                    order = Order.objects.create(
                        requirement=requirement, quote=quote, supplier=quote.supplier, customer=customer,
                    )
                    # Reflect that the requirement already has a selected quote
                    # and is moving straight into production.
                    try:
                        order.mark_quoted()
                        order.select_quote()
                        order.start_production()
                        order.save()
                    except Exception:
                        pass
        return redirect(reverse('requirement', kwargs={'pk': requirement.id}))


class OrderListView(ListView):
    model = Order
    template_name = "order/order_list.html"
    context_object_name = 'bills'
    ordering = ['-created_at']
    paginate_by = 10

    def get(self, request):
        user = self.request.user
        if self.request.user.is_staff:
            supplier = ManufacturerProfile.objects.filter(user=user).first()
            orders = Order.objects.filter(supplier=supplier)
        else:
            customer = ConsumerProfile.objects.filter(user=user).first()
            orders = Order.objects.filter(customer=customer)
        context = {'bills': orders}
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return render(request, self.template_name, context)


class OrderDetailView(View):
    template_name = "order/order_detail.html"

    def get(self, request, billno):
        order = get_object_or_404(Order, billno=billno)
        requirement = order.requirement
        items = RequirementPart.objects.filter(requirement=requirement.id)
        quote = order.quote
        supplier = order.supplier
        customer = order.customer
        total = 0
        for each in items:
            total += each.quantity
        total = total * quote.quote_price
        context = {
            'bill': order,
            'demand': requirement,
            'items': items,
            'quote': quote,
            'supplier': supplier,
            'customer': customer,
            'total': total,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return render(request, self.template_name, context)


class global_search_view(LoginRequiredMixin, ListView):
    model = Requirement
    template_name = "globalsearch.html"
    paginate_by = 10

    def get_queryset(self):
        query = self.request.GET.get('search')
        fieldlist = ['user', 'title', 'rfq_desc', 'quote_currency', 'request_reason', 'parts', 'end_date', 'industry', 'file']
        split_query = query.split(' ', 1) if query else []
        search_field = 'title'
        search_value = ''
        if len(split_query) >= 2:
            search_field = split_query[0]
            if search_field not in fieldlist:
                search_field = 'title'
            search_value = split_query[1]
        elif len(split_query) == 1:
            search_value = split_query[0]
        if search_value:
            queryset = Requirement.objects.filter(is_deleted=False)
            return queryset.filter(**{f"{search_field}__icontains": search_value})
        else:
            queryset = Requirement.objects.filter(id=0, is_deleted=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context
