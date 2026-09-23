import logging

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

from . import services
from .models import (
    Requirement, RequirementPart, Quote, Order,
    QCChecklistItem, RequirementQuestion, RFQDecline,
)
from .forms import (
    SelectRequirement, RequirementPartInlineFormSet, QuoteForm,
    ShipmentForm, ProductionUpdateForm, RequirementQuestionForm,
)

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)

logger = logging.getLogger(__name__)

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
    paginate_by = 10

    def get_template_names(self):
        if self.request.user.role == 'manufacturer':
            return ["requirement/rfq_inbox.html"]
        return ["requirement/requirement_list.html"]

    def _manufacturer_supplier(self):
        return ManufacturerProfile.objects.filter(user=self.request.user).first()

    def get_queryset(self):
        user = self.request.user
        sort = self.request.GET.get('sort', '')
        industry_id = self.request.GET.get('industry', '')
        if user.role == 'manufacturer':
            supplier = self._manufacturer_supplier()
            tab = self.request.GET.get('tab', 'new')
            if tab == 'quoted':
                queryset = Requirement.objects.filter(
                    is_deleted=False, quote__supplier=supplier,
                    quote__is_selected=False, quote__status__isnull=True,
                ).distinct()
            elif tab == 'won':
                queryset = Requirement.objects.filter(
                    is_deleted=False, quote__supplier=supplier, quote__is_selected=True,
                ).distinct()
            elif tab == 'lost':
                queryset = Requirement.objects.filter(
                    is_deleted=False, quote__supplier=supplier, quote__status='Rejected',
                ).distinct()
            else:
                queryset = services.open_requirements_for(supplier)
                if supplier:
                    queryset = queryset.exclude(quote__supplier=supplier).exclude(declines__supplier=supplier)
            process = self.request.GET.get('process', '')
            if process:
                queryset = queryset.filter(requirement_parts__technology=process).distinct()
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
        elif sort == 'cr_date_asc':
            queryset = queryset.order_by('created_at')
        elif sort == 'cr_date_desc':
            queryset = queryset.order_by('-created_at')
        elif sort == 'match_desc' and user.role == 'manufacturer':
            # Match% is computed in Python, not stored — sort over the full
            # tab result set before pagination (documented trade-off: other
            # sorts stay lazy/DB-ordered, this one materializes the list).
            supplier = self._manufacturer_supplier()
            scored = list(queryset)
            match_map = services.bulk_match_percent(scored, supplier) if supplier else {}
            scored.sort(key=lambda r: match_map.get(r.pk) or -1, reverse=True)
            return scored
        else:
            queryset = queryset.order_by('-pk')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sort'] = self.request.GET.get('sort', '')
        user = self.request.user
        if user.role == 'manufacturer':
            supplier = self._manufacturer_supplier()
            context['tab'] = self.request.GET.get('tab', 'new')
            context['process'] = self.request.GET.get('process', '')
            context['tab_counts'] = {'new': 0, 'quoted': 0, 'won': 0, 'lost': 0}
            if supplier:
                context['tab_counts'] = {
                    'new': services.open_requirements_for(supplier).exclude(
                        quote__supplier=supplier
                    ).exclude(declines__supplier=supplier).count(),
                    'quoted': Requirement.objects.filter(
                        is_deleted=False, quote__supplier=supplier,
                        quote__is_selected=False, quote__status__isnull=True,
                    ).distinct().count(),
                    'won': Requirement.objects.filter(
                        is_deleted=False, quote__supplier=supplier, quote__is_selected=True,
                    ).distinct().count(),
                    'lost': Requirement.objects.filter(
                        is_deleted=False, quote__supplier=supplier, quote__status='Rejected',
                    ).distinct().count(),
                }
                match_map = services.bulk_match_percent(context['object_list'], supplier)
                for requirement in context['object_list']:
                    requirement.match_percent = match_map.get(requirement.pk)
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
                logger.info("Requirement #%s created by %s", requirement.pk, request.user)
                messages.success(request, self.success_message)
                return redirect(self.success_url)
            # Parts were invalid — undo the just-created requirement and re-show the form.
            logger.warning(
                "RequirementPart formset invalid for requirement #%s by %s, rolling back: %s",
                requirement.pk, request.user, formset.errors,
            )
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


class RFQDeclineView(LoginRequiredMixin, View):
    def post(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk)
        supplier = get_object_or_404(ManufacturerProfile, user=request.user)
        RFQDecline.objects.get_or_create(
            requirement=requirement, supplier=supplier,
            defaults={'reason': request.POST.get('reason', '')},
        )
        logger.info("Requirement #%s declined by supplier #%s", requirement.pk, supplier.pk)
        messages.success(request, "RFQ declined.")
        return redirect(reverse('requirement-list'))


class RequirementQuestionListCreateView(LoginRequiredMixin, View):
    def get(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk)
        supplier = ManufacturerProfile.objects.filter(user=request.user).first()
        questions = RequirementQuestion.objects.filter(requirement=requirement, supplier=supplier) if supplier else []
        return render(request, 'requirement/_questions_section.html', {
            'demand': requirement, 'questions': questions, 'question_form': RequirementQuestionForm(),
        })

    def post(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk)
        supplier = get_object_or_404(ManufacturerProfile, user=request.user)
        form = RequirementQuestionForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)
            question.requirement = requirement
            question.supplier = supplier
            question.asked_by = request.user
            question.save()
        questions = RequirementQuestion.objects.filter(requirement=requirement, supplier=supplier)
        return render(request, 'requirement/_questions_section.html', {
            'demand': requirement, 'questions': questions, 'question_form': RequirementQuestionForm(),
        })


class RequirementQuestionAnswerView(LoginRequiredMixin, View):
    def post(self, request, pk):
        question = get_object_or_404(RequirementQuestion, pk=pk)
        if request.user != question.requirement.user:
            messages.error(request, "Only the buyer who posted this RFQ can answer questions on it.")
            return redirect(reverse('requirement', kwargs={'pk': question.requirement.pk}))
        question.answer = request.POST.get('answer', '')
        question.answered_at = timezone.now()
        question.save()
        return redirect(reverse('requirement', kwargs={'pk': question.requirement.pk}))


class QuoteListView(ListView):
    model = Quote
    template_name = "quote/quote_list.html"
    paginate_by = 10

    def get_queryset(self):
        user = self.request.user.id
        supplier = ManufacturerProfile.objects.filter(user=user).first()
        queryset = Quote.objects.filter(is_deleted=False, supplier=supplier)
        return queryset


class QuoteCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Quote
    form_class = QuoteForm
    success_url = '/marketplace/quote'
    success_message = "Quotation has been created successfully"
    template_name = "quote/quote_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New Quote'
        context["savebtn"] = 'Submit quote'
        requirement = Requirement.objects.filter(pk=self.kwargs.get('pk')).first()
        context["demand"] = requirement
        context["parts"] = requirement.requirement_parts.all() if requirement else []
        context["total_quantity"] = requirement.total_parts_quantity() if requirement else 0
        supplier = ManufacturerProfile.objects.filter(user=self.request.user).first()
        context["match_percent"] = services.compute_match_percent(requirement, supplier) if (requirement and supplier) else None
        context["questions"] = RequirementQuestion.objects.filter(requirement=requirement, supplier=supplier) if (requirement and supplier) else []
        context["question_form"] = RequirementQuestionForm()
        return context

    def post(self, request, *args, **kwargs):
        # Fixed bug: this view used to bypass form validation entirely,
        # hand-building a Quote from raw POST data with no validation on
        # quote_price. `requirement`/`supplier` are assigned here from the
        # URL/session — never taken from the form — so a manufacturer can't
        # submit a quote as someone else or against a requirement they
        # didn't open.
        self.object = None
        requirement = get_object_or_404(Requirement, pk=self.kwargs.get('pk'))
        supplier_details = get_object_or_404(ManufacturerProfile, user=request.user)
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        quote = form.save(commit=False)
        quote.requirement = requirement
        quote.supplier = supplier_details
        quote.is_draft = request.POST.get('action') == 'draft'
        quote.save()
        logger.info("Quote #%s submitted for requirement #%s by %s", quote.pk, requirement.pk, request.user)
        messages.success(request, "Quote saved as draft." if quote.is_draft else self.success_message)
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


class QuoteUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Quote
    form_class = QuoteForm
    success_url = '/marketplace/quote'
    success_message = "Quotation details has been updated successfully"
    template_name = "quote/quote_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Quote'
        context["savebtn"] = 'Save changes'
        context["delbtn"] = 'Delete Quote'
        requirement = self.object.requirement
        context["demand"] = requirement
        context["parts"] = requirement.requirement_parts.all()
        context["total_quantity"] = requirement.total_parts_quantity()
        context["match_percent"] = services.compute_match_percent(requirement, self.object.supplier)
        context["questions"] = RequirementQuestion.objects.filter(requirement=requirement, supplier=self.object.supplier)
        context["question_form"] = RequirementQuestionForm()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        quote = form.save(commit=False)
        quote.is_draft = request.POST.get('action') == 'draft'
        quote.save()
        messages.success(request, "Quote saved as draft." if quote.is_draft else self.success_message)
        return redirect(reverse('requirement', kwargs={'pk': quote.requirement.pk}))


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
            logger.info(
                "Quote #%s selected for requirement #%s by %s (%d other quote(s) auto-rejected)",
                quote.pk, requirement.pk, request.user, other_quotes.count(),
            )
        elif status == 'Rejected':
            quote.status = 'Rejected'
            logger.info("Quote #%s rejected by %s", quote.pk, request.user)
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
                    logger.info("Order #%s auto-created for requirement #%s", order.billno, requirement.pk)
                    # Reflect that the requirement already has a selected quote
                    # and is moving straight into production.
                    try:
                        order.mark_quoted()
                        order.select_quote()
                        order.start_production()
                        order.save()
                    except Exception:
                        logger.exception(
                            "Failed to fast-forward order #%s to in_production for requirement #%s",
                            order.billno, requirement.pk,
                        )
                else:
                    logger.warning(
                        "Requirement #%s marked Completed but has no selected quote — no Order created",
                        requirement.pk,
                    )
        return redirect(reverse('requirement', kwargs={'pk': requirement.id}))

    def get(self, request, pk, status):
        return self._update(request, pk, status)

    def post(self, request, pk, status):
        return self._update(request, pk, status)


class OrderListView(ListView):
    model = Order
    context_object_name = 'bills'
    ordering = ['-created_at']
    paginate_by = 10

    def get(self, request):
        user = self.request.user
        if user.role == 'manufacturer':
            supplier = ManufacturerProfile.objects.filter(user=user).first()
            orders = Order.objects.filter(supplier=supplier)
            template_name = "order/order_list_manufacturer.html"
        else:
            customer = ConsumerProfile.objects.filter(user=user).first()
            orders = Order.objects.filter(customer=customer)
            template_name = "order/order_list.html"
        context = {'bills': orders.order_by('-created_at')}
        return render(request, template_name, context)


class OrderDetailView(View):
    template_name = "order/order_detail.html"

    def get(self, request, billno):
        order = get_object_or_404(Order, billno=billno)
        requirement = order.requirement
        items = RequirementPart.objects.filter(requirement=requirement.id)
        quote = order.quote
        supplier = order.supplier
        customer = order.customer
        breakdown = quote.get_breakdown()
        next_status = _next_order_status(order)
        is_supplier = request.user.is_authenticated and request.user == order.supplier.user
        can_advance = request.user.is_authenticated and (
            request.user == order.supplier.user or request.user == order.customer.user
        )
        can_manage_production = is_supplier and order.status in ('in_production', 'payment_pending', 'paid')
        context = {
            'bill': order,
            'demand': requirement,
            'items': items,
            'quote': quote,
            'supplier': supplier,
            'customer': customer,
            'breakdown': breakdown,
            'total': breakdown['total'],
            'next_status': next_status,
            'can_advance': can_advance,
            'is_supplier': is_supplier,
            'can_manage_production': can_manage_production,
            'production_stages': Order.PRODUCTION_STAGE_CHOICES,
            'current_stage_index': Order.PRODUCTION_STAGES.index(order.production_stage),
            'next_production_stage': order.next_production_stage(),
            'production_progress_percent': order.production_progress_percent(),
            'qc_items': order.qc_items.all(),
            'updates': order.updates.select_related('author').all(),
            'update_form': ProductionUpdateForm(),
            'shipment_form': ShipmentForm(instance=order),
        }
        return render(request, self.template_name, context)


class OrderProductionAdvanceView(LoginRequiredMixin, View):
    def post(self, request, billno):
        order = get_object_or_404(Order, billno=billno)
        if request.user != order.supplier.user:
            messages.error(request, "Only the manufacturer on this order can update production.")
            return redirect(reverse('order-detail', kwargs={'billno': order.billno}))
        advanced = order.advance_production_stage(note=request.POST.get('note', ''))
        if advanced:
            messages.success(request, f"Marked '{order.get_production_stage_display()}' complete.")
        if getattr(request, 'htmx', False):
            return render(request, 'order/_production_tracker.html', {
                'bill': order,
                'production_stages': Order.PRODUCTION_STAGE_CHOICES,
                'current_stage_index': Order.PRODUCTION_STAGES.index(order.production_stage),
                'next_production_stage': order.next_production_stage(),
                'production_progress_percent': order.production_progress_percent(),
                'can_manage_production': order.status in ('in_production', 'payment_pending', 'paid'),
            })
        return redirect(reverse('order-detail', kwargs={'billno': order.billno}))


class OrderUpdateCreateView(LoginRequiredMixin, View):
    def post(self, request, billno):
        order = get_object_or_404(Order, billno=billno)
        if request.user != order.supplier.user:
            messages.error(request, "Only the manufacturer on this order can post updates.")
            return redirect(reverse('order-detail', kwargs={'billno': order.billno}))
        form = ProductionUpdateForm(request.POST, request.FILES)
        if form.is_valid():
            update = form.save(commit=False)
            update.order = order
            update.author = request.user
            update.save()
        if getattr(request, 'htmx', False):
            return render(request, 'order/_updates_feed.html', {
                'updates': order.updates.select_related('author').all(),
                'update_form': ProductionUpdateForm(),
                'bill': order,
                'is_supplier': True,
            })
        return redirect(reverse('order-detail', kwargs={'billno': order.billno}))


class QCChecklistToggleView(LoginRequiredMixin, View):
    def post(self, request, billno, item_pk):
        order = get_object_or_404(Order, billno=billno)
        item = get_object_or_404(QCChecklistItem, pk=item_pk, order=order)
        if request.user != order.supplier.user:
            messages.error(request, "Only the manufacturer on this order can update the QC checklist.")
            return redirect(reverse('order-detail', kwargs={'billno': order.billno}))
        item.is_checked = not item.is_checked
        item.checked_at = timezone.now() if item.is_checked else None
        item.checked_by = request.user if item.is_checked else None
        item.save()
        if getattr(request, 'htmx', False):
            return render(request, 'order/_qc_checklist.html', {'qc_items': order.qc_items.all(), 'bill': order, 'is_supplier': True})
        return redirect(reverse('order-detail', kwargs={'billno': order.billno}))


class OrderShipmentUpdateView(LoginRequiredMixin, View):
    def post(self, request, billno):
        order = get_object_or_404(Order, billno=billno)
        if request.user != order.supplier.user:
            messages.error(request, "Only the manufacturer on this order can update shipment details.")
            return redirect(reverse('order-detail', kwargs={'billno': order.billno}))
        form = ShipmentForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, "Shipment details updated.")
        if getattr(request, 'htmx', False):
            return render(request, 'order/_shipment_form.html', {'bill': order, 'shipment_form': ShipmentForm(instance=order), 'is_supplier': True})
        return redirect(reverse('order-detail', kwargs={'billno': order.billno}))


class OrderStatusUpdateView(LoginRequiredMixin, View):
    def post(self, request, billno, status):
        order = get_object_or_404(Order, billno=billno)
        transition_name = ORDER_TRANSITIONS.get(status)
        if transition_name and hasattr(order, transition_name):
            transition_method = getattr(order, transition_name)
            try:
                transition_method(note=request.POST.get('note', ''))
                order.save()
                logger.info("Order #%s -> %s by %s", order.billno, order.status, request.user)
                messages.success(request, f"Order moved to {order.get_status_display()}.")
            except TransitionNotAllowed:
                logger.warning(
                    "Rejected transition '%s' on order #%s (current status: %s) by %s",
                    status, order.billno, order.status, request.user,
                )
                messages.error(request, "That status change isn't allowed from the order's current state.")
        else:
            logger.warning("Unknown order status '%s' requested for order #%s", status, billno)
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
