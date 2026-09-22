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
from django.utils import timezone
from django.conf import settings
from django.apps import apps
from django_fsm import TransitionNotAllowed

from accounts.models import ManufacturerProfile, ConsumerProfile

from .models import Requirement, RequirementPart, Quote, Order
from .forms import SelectRequirement, RequirementPartInlineFormSet, SelectQuote

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)

# Order.status transitions keyed by the target status they move the order to,
# mapping onto the django_fsm transition methods defined on the model.
ORDER_TRANSITIONS = {
    'quoted': 'mark_quoted',
    'quote_selected': 'select_quote',
    'in_production': 'start_production',
    'payment_pending': 'request_payment',
    'paid': 'mark_paid',
    'completed': 'complete',
    'cancelled': 'cancel',
}


def _next_order_status(order):
    """The single next non-cancel transition available from the order's
    current FSM state, if any (e.g. quote_selected -> in_production)."""
    available = [t.target for t in order.get_available_status_transitions() if t.target != 'cancelled']
    return available[0] if available else None


class RequirementListStatusView(LoginRequiredMixin, ListView):
    model = Requirement
    template_name = "requirement/requirement_list.html"
    paginate_by = 10

    def get_queryset(self):
        status = self.kwargs.get('status')
        user = self.request.user
        if user.role == 'manufacturer':
            supplier = ManufacturerProfile.objects.filter(user=user).first()
            requirement = Requirement.objects.filter(
                is_deleted=False, status=status, quote__supplier=supplier, quote__is_selected=True
            )
        else:
            requirement = Requirement.objects.filter(is_deleted=False, status=status, user=user)
        return requirement


class RequirementListView(LoginRequiredMixin, ListView):
    model = Requirement
    template_name = "requirement/requirement_list.html"
    paginate_by = 10

    def get_queryset(self):
        user = self.request.user
        sort = self.request.GET.get('sort', '')
        industry_id = self.request.GET.get('industry', '')
        if user.role == 'manufacturer':
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
        return context


class RequirementCreateView(SuccessMessageMixin, CreateView):
    model = Requirement
    form_class = SelectRequirement
    template_name = "requirement/edit_requirement.html"
    success_url = '/marketplace/requirement'
    success_message = "RFQ has been created successfully"

    def get_initial(self):
        initial = super().get_initial()
        initial['user'] = self.request.user.pk
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New RFQ'
        context["savebtn"] = 'Add RFQ'
        if "formset" not in context:
            context["formset"] = RequirementPartInlineFormSet(instance=Requirement())
        return context

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if form.is_valid():
            requirement = form.save()
            formset = RequirementPartInlineFormSet(request.POST, request.FILES, instance=requirement)
            if formset.is_valid():
                formset.save()
                messages.success(request, self.success_message)
                return redirect(self.success_url)
            # Parts were invalid — undo the just-created requirement and re-show the form.
            requirement.delete()
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
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
        if "formset" not in context:
            context["formset"] = RequirementPartInlineFormSet(instance=self.object)
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        formset = RequirementPartInlineFormSet(request.POST, request.FILES, instance=self.object)
        if form.is_valid() and formset.is_valid():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            messages.success(request, self.success_message)
            return redirect(self.success_url)
        return self.render_to_response(self.get_context_data(form=form, formset=formset))


class RequirementDeleteView(View):
    template_name = "requirement/delete_requirement.html"
    success_message = "Requirement Record has been deleted successfully"

    def get(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk)
        return render(request, self.template_name, {'object': requirement})

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
        quotes = Quote.objects.filter(requirement=requirement, is_deleted=False)
        is_manufacturer = request.user.is_authenticated and request.user.role == 'manufacturer'
        my_quote = None
        if is_manufacturer:
            supplier = ManufacturerProfile.objects.filter(user=request.user).first()
            my_quote = quotes.filter(supplier=supplier).first()
        return render(request, 'requirement/requirement.html', {
            'demand': requirement,
            'quotes': quotes,
            'demanddetails': requirement_parts,
            'is_manufacturer': is_manufacturer,
            'my_quote': my_quote,
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
        context["demand"] = Requirement.objects.filter(pk=self.kwargs.get('pk')).first()
        return context

    def post(self, request, *args, **kwargs):
        quote_price = request.POST.get('quote_price')
        note = request.POST.get('note')
        pk = self.kwargs.get('pk')
        requirement = get_object_or_404(Requirement, pk=pk)
        supplier_details = get_object_or_404(ManufacturerProfile, user=request.user)
        quote = Quote(
            requirement=requirement,
            supplier=supplier_details,
            quote_price=quote_price,
            note=note
        )
        quote.save()
        messages.success(request, self.success_message)
        if getattr(request, 'htmx', False):
            response = render(request, 'requirement/_quotes_section.html', {
                'demand': requirement,
                'quotes': Quote.objects.filter(requirement=requirement, is_deleted=False),
                'is_manufacturer': True,
                'my_quote': quote,
            })
            response['HX-Redirect'] = reverse('requirement', kwargs={'pk': requirement.pk})
            return response
        return redirect(reverse('requirement', kwargs={'pk': requirement.pk}))


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
        return context


class QuoteDeleteView(View):
    template_name = "quote/delete_quote.html"
    success_message = "Quotation has been deleted successfully"

    def get(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        return render(request, self.template_name, {'object': quote})

    def post(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        quote.is_deleted = True
        quote.save()
        messages.success(request, self.success_message)
        return redirect('quote-list')


class QuoteView(View):
    def get(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        return render(request, 'quote/quote.html', {'quote': quote})


class QuoteStatusUpdateView(View):
    def _update(self, request, pk, status):
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
        if getattr(request, 'htmx', False):
            is_manufacturer = request.user.is_authenticated and request.user.role == 'manufacturer'
            my_quote = None
            if is_manufacturer:
                supplier = ManufacturerProfile.objects.filter(user=request.user).first()
                my_quote = Quote.objects.filter(requirement=requirement, supplier=supplier).first()
            return render(request, 'requirement/_quotes_section.html', {
                'demand': requirement,
                'quotes': Quote.objects.filter(requirement=requirement, is_deleted=False),
                'is_manufacturer': is_manufacturer,
                'my_quote': my_quote,
            })
        return redirect(reverse('requirement', kwargs={'pk': requirement.id}))

    def get(self, request, pk, status):
        return self._update(request, pk, status)

    def post(self, request, pk, status):
        return self._update(request, pk, status)


class RequirementStatusUpdateView(View):
    def _update(self, request, pk, status):
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

    def get(self, request, pk, status):
        return self._update(request, pk, status)

    def post(self, request, pk, status):
        return self._update(request, pk, status)


class OrderListView(ListView):
    model = Order
    template_name = "order/order_list.html"
    context_object_name = 'bills'
    ordering = ['-created_at']
    paginate_by = 10

    def get(self, request):
        user = self.request.user
        if user.role == 'manufacturer':
            supplier = ManufacturerProfile.objects.filter(user=user).first()
            orders = Order.objects.filter(supplier=supplier)
        else:
            customer = ConsumerProfile.objects.filter(user=user).first()
            orders = Order.objects.filter(customer=customer)
        context = {'bills': orders.order_by('-created_at')}
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
        next_status = _next_order_status(order)
        can_advance = request.user.is_authenticated and (
            request.user == order.supplier.user or request.user == order.customer.user
        )
        context = {
            'bill': order,
            'demand': requirement,
            'items': items,
            'quote': quote,
            'supplier': supplier,
            'customer': customer,
            'total': total,
            'next_status': next_status,
            'can_advance': can_advance,
        }
        return render(request, self.template_name, context)


class OrderStatusUpdateView(LoginRequiredMixin, View):
    def post(self, request, billno, status):
        order = get_object_or_404(Order, billno=billno)
        transition_name = ORDER_TRANSITIONS.get(status)
        if transition_name and hasattr(order, transition_name):
            transition_method = getattr(order, transition_name)
            try:
                transition_method(note=request.POST.get('note', ''))
                order.save()
                messages.success(request, f"Order moved to {order.get_status_display()}.")
            except TransitionNotAllowed:
                messages.error(request, "That status change isn't allowed from the order's current state.")
        else:
            messages.error(request, "Unknown order status.")
        if getattr(request, 'htmx', False):
            can_advance = request.user.is_authenticated and (
                request.user == order.supplier.user or request.user == order.customer.user
            )
            return render(request, 'order/_order_status.html', {
                'bill': order,
                'next_status': _next_order_status(order),
                'can_advance': can_advance,
            })
        return redirect(reverse('order-detail', kwargs={'billno': order.billno}))


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
        return context
