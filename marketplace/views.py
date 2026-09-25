import datetime
import logging
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
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
from django.db import transaction
from django.db.models import Q
from django.apps import apps
from django_fsm import TransitionNotAllowed

from accounts.models import ManufacturerProfile, ConsumerProfile

from . import services
from .models import (
    Requirement, RequirementPart, Quote, Order,
    RFQDecline,
    MessageThread, Message,
    RequirementAmendment, AmendmentResponse,
)
from .forms import (
    SelectRequirement, RequirementPartInlineFormSet, QuoteForm,
    ShipmentForm, ProductionUpdateForm, MessageForm, AmendmentAcceptForm,
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
            tab = self.request.GET.get('tab', 'all')
            if tab == 'collecting':
                queryset = queryset.filter(status__isnull=True, quote__isnull=True)
            elif tab == 'ready':
                queryset = queryset.filter(status__isnull=True, quote__isnull=False).distinct()
            elif tab == 'awarded':
                queryset = queryset.filter(status__in=['Approved', 'Production', 'Completed'])
            search = self.request.GET.get('q', '')
            if search:
                queryset = queryset.filter(title__icontains=search)
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
        else:
            own = Requirement.objects.filter(user=user, is_deleted=False)
            context['tab'] = self.request.GET.get('tab', 'all')
            context['q'] = self.request.GET.get('q', '')
            context['tab_counts'] = {
                'all': own.count(),
                'collecting': own.filter(status__isnull=True, quote__isnull=True).count(),
                'ready': own.filter(status__isnull=True, quote__isnull=False).distinct().count(),
                'awarded': own.filter(status__in=['Approved', 'Production', 'Completed']).count(),
            }
        return context


def _own_open_requirement(request, pk):
    """The logged-in buyer's own RFQ, still open for editing (not yet
    awarded). Anyone else gets a 404, so RFQ ids can't be probed."""
    requirement = get_object_or_404(Requirement, pk=pk, is_deleted=False)
    if requirement.user_id != request.user.id:
        raise Http404
    if requirement.status:
        raise PermissionDenied("This RFQ has already been awarded and can no longer be changed.")
    return requirement


def _sync_part_count(requirement):
    requirement.parts = requirement.requirement_parts.count()
    requirement.save(update_fields=['parts'])


class RequirementCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Requirement
    form_class = SelectRequirement
    template_name = "requirement/edit_requirement.html"
    success_url = '/marketplace/requirement'
    success_message = "RFQ has been created successfully"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.user.role == 'manufacturer':
                raise PermissionDenied("Only buyers can post RFQs.")
            limit, used = services.rfq_allowance(request.user)
            if limit is not None and used >= limit:
                messages.error(
                    request,
                    f"You've used all {limit} RFQs included in your plan this month. Upgrade your plan to post more.",
                )
                return redirect(reverse('profile') + '?tab=billing')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New RFQ'
        context["savebtn"] = 'Add RFQ'
        if "formset" not in context:
            context["formset"] = RequirementPartInlineFormSet(instance=Requirement())
        return context

    def post(self, request, *args, **kwargs):
        self.object = None
        form = self.get_form()
        if form.is_valid():
            requirement = form.save(commit=False)
            requirement.user = request.user
            requirement.save()
            formset = RequirementPartInlineFormSet(request.POST, request.FILES, instance=requirement)
            if formset.is_valid():
                formset.save()
                _sync_part_count(requirement)
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


class RequirementUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Requirement
    form_class = SelectRequirement
    success_url = '/marketplace/requirement'
    success_message = "RFQ details has been updated successfully"
    template_name = "requirement/edit_requirement.html"

    def get_object(self, queryset=None):
        return _own_open_requirement(self.request, self.kwargs['pk'])

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            requirement = _own_open_requirement(request, kwargs['pk'])
            if requirement.pending_amendment():
                messages.error(request, "Suppliers are still responding to your last change request. Withdraw it first to make more changes.")
                return redirect(reverse('requirement', kwargs={'pk': requirement.pk}) + '#changes')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Requirement'
        context["savebtn"] = 'Save Changes'
        context["delbtn"] = 'Delete Requirement'
        context["live_quote_count"] = self.object.live_quotes().count()
        if "formset" not in context:
            context["formset"] = RequirementPartInlineFormSet(instance=self.object)
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        formset = RequirementPartInlineFormSet(request.POST, request.FILES, instance=self.object)
        if not (form.is_valid() and formset.is_valid()):
            return self.render_to_response(self.get_context_data(form=form, formset=formset))

        live_quotes = list(self.object.live_quotes())
        if not live_quotes:
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            _sync_part_count(self.object)
            messages.success(request, self.success_message)
            return redirect(self.success_url)

        # Suppliers have priced the current version: stage the edit as a
        # change request instead of changing the RFQ under their quotes.
        changes, new_end_date, unsupported = services.diff_rfq_edit(self.object, form, formset)
        if unsupported:
            form.add_error(None, (
                f"Suppliers have already quoted on this RFQ, so {', '.join(unsupported)} can't be changed here. "
                "Message the suppliers, or post a new RFQ for the new parts or files."
            ))
            self.object = Requirement.objects.get(pk=self.object.pk)
            return self.render_to_response(self.get_context_data(form=form, formset=formset))

        back = reverse('requirement', kwargs={'pk': self.object.pk})
        if new_end_date is not None:
            Requirement.objects.filter(pk=self.object.pk).update(end_date=new_end_date)
        if changes:
            with transaction.atomic():
                amendment = RequirementAmendment.objects.create(requirement_id=self.object.pk, proposed_by=request.user, changes=changes)
                AmendmentResponse.objects.bulk_create([AmendmentResponse(amendment=amendment, quote=quote) for quote in live_quotes])
            logger.info("Change request #%s on requirement #%s sent to %d supplier(s)", amendment.pk, self.object.pk, len(live_quotes))
            messages.success(request, (
                f"Changes sent to {len(live_quotes)} supplier{'s' if len(live_quotes) != 1 else ''} for confirmation. "
                "The RFQ keeps its current details until you accept a supplier's updated pricing."
                + (" The new due date is already in effect." if new_end_date is not None else "")
            ))
            return redirect(back + '#changes')
        messages.success(request, "Due date updated." if new_end_date is not None else "Nothing changed.")
        return redirect(back)


class RequirementExtendView(LoginRequiredMixin, View):
    """Buyer moves an RFQ's due date (e.g. after it closed with no quotes).
    A future date reopens it: it's back in suppliers' inboxes."""
    http_method_names = ['post']

    def post(self, request, pk):
        requirement = _own_open_requirement(request, pk)
        back = reverse('requirement', kwargs={'pk': requirement.pk})
        try:
            new_date = datetime.date.fromisoformat(request.POST.get('end_date', ''))
        except ValueError:
            messages.error(request, "Pick a new due date.")
            return redirect(back)
        new_end = timezone.make_aware(datetime.datetime.combine(new_date, datetime.time(23, 59)))
        if new_end <= timezone.now():
            messages.error(request, "The new due date must be in the future.")
            return redirect(back)
        requirement.end_date = new_end
        requirement.save(update_fields=['end_date', 'updated_at'])
        logger.info("Requirement #%s due date moved to %s by %s", requirement.pk, new_date, request.user)
        messages.success(request, f"Due date moved to {new_date:%d %b %Y}. The RFQ is open to suppliers again.")
        return redirect(back)


class AmendmentWithdrawView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        amendment = get_object_or_404(RequirementAmendment.objects.select_related('requirement'), pk=pk)
        if amendment.requirement.user_id != request.user.id:
            raise Http404
        if amendment.status == RequirementAmendment.PENDING:
            amendment.close(RequirementAmendment.WITHDRAWN)
            messages.success(request, "Change request withdrawn. The RFQ keeps its current details.")
        return redirect(reverse('requirement', kwargs={'pk': amendment.requirement_id}) + '#changes')


class AmendmentRespondView(LoginRequiredMixin, View):
    """Supplier accepts the buyer's changes with updated pricing, or rejects them."""
    http_method_names = ['post']

    def post(self, request, pk):
        response = get_object_or_404(
            AmendmentResponse.objects.select_related('amendment__requirement', 'quote__supplier'), pk=pk,
        )
        if response.quote.supplier.user_id != request.user.id:
            raise Http404
        back = reverse('requirement', kwargs={'pk': response.amendment.requirement_id}) + '#changes'
        if response.status != AmendmentResponse.PENDING or not response.quote.is_undecided:
            messages.error(request, "This change request is no longer open.")
            return redirect(back)

        if request.POST.get('action') == 'reject':
            response.status = AmendmentResponse.REJECTED
            response.supplier_note = request.POST.get('supplier_note', '').strip()[:1000]
            response.responded_at = timezone.now()
            response.save()
            messages.success(request, "You rejected the changes. The buyer has been notified and your current quote stands.")
            return redirect(back)

        form = AmendmentAcceptForm(request.POST, instance=response)
        if not form.is_valid():
            for errors in form.errors.values():
                messages.error(request, errors.as_text().lstrip('* '))
            return redirect(back)
        response = form.save(commit=False)
        response.status = AmendmentResponse.ACCEPTED
        response.responded_at = timezone.now()
        response.save()
        messages.success(request, "New pricing sent. Your quote updates only if the buyer accepts it.")
        return redirect(back)


class AmendmentDecisionView(LoginRequiredMixin, View):
    """Buyer accepts a supplier's updated pricing or keeps the original
    details. Accepting is choosing that supplier: the RFQ changes are
    applied, the quote takes the new pricing, and the quote is awarded
    exactly as "Select this quote" would (others rejected, order created)."""
    http_method_names = ['post']

    def post(self, request, pk):
        response = get_object_or_404(
            AmendmentResponse.objects.select_related('amendment__requirement', 'quote__supplier'), pk=pk,
        )
        amendment = response.amendment
        requirement = amendment.requirement
        if requirement.user_id != request.user.id:
            raise Http404
        back = reverse('requirement', kwargs={'pk': requirement.pk}) + '#changes'
        if (response.status != AmendmentResponse.ACCEPTED or amendment.status != RequirementAmendment.PENDING
                or requirement.status or not response.quote.is_undecided):
            messages.error(request, "This pricing can no longer be accepted.")
            return redirect(back)

        supplier_name = response.quote.supplier.companyname or response.quote.supplier
        if request.POST.get('decision') == 'accept':
            with transaction.atomic():
                amendment.apply()
                response.apply_pricing()
                response.status = AmendmentResponse.BUYER_ACCEPTED
                response.buyer_decided_at = timezone.now()
                response.save()
                requirement.refresh_from_db()
                order, rejected = services.award_quote(requirement, response.quote)
            logger.info(
                "Change request #%s applied and quote #%s awarded at new pricing by %s (%d other quote(s) rejected, order #%s)",
                amendment.pk, response.quote_id, request.user, rejected, getattr(order, 'billno', None),
            )
            messages.success(request, f"RFQ updated and awarded to {supplier_name} at the new pricing. The order has been created.")
        else:
            response.status = AmendmentResponse.BUYER_DECLINED
            response.buyer_decided_at = timezone.now()
            response.save()
            messages.success(request, f"Kept {supplier_name}'s original quote.")
        return redirect(back)


class RequirementDeleteView(LoginRequiredMixin, View):
    template_name = "requirement/delete_requirement.html"
    success_message = "Requirement Record has been deleted successfully"

    def get(self, request, pk):
        requirement = _own_open_requirement(request, pk)
        return render(request, self.template_name, {'object': requirement})

    def post(self, request, pk):
        requirement = _own_open_requirement(request, pk)
        requirement.is_deleted = True
        requirement.save()
        messages.success(request, self.success_message)
        return redirect('requirement-list')


class RequirementView(LoginRequiredMixin, View):
    def get(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk)
        # Manufacturers browse RFQs to quote on them; a buyer may only open
        # their own.
        if request.user.role != 'manufacturer' and not request.user.is_staff and requirement.user_id != request.user.id:
            raise Http404
        context = quotes_section_context(request, requirement)
        context['demanddetails'] = RequirementPart.objects.filter(requirement=requirement).all()
        context.update(_rfq_conversation_context(request, requirement))
        context.update(_change_request_context(request, requirement, context.get('my_quote')))
        return render(request, 'requirement/requirement.html', context)


def _change_request_context(request, requirement, my_quote):
    """Change requests for the RFQ page: the buyer sees every request with
    every supplier's response; a supplier sees only their own responses."""
    if request.user.id == requirement.user_id:
        amendments = list(requirement.amendments.prefetch_related('responses__quote__supplier'))
        return {
            'amendments': amendments,
            'pending_amendment': next((a for a in amendments if a.status == RequirementAmendment.PENDING), None),
            'expired_no_quotes': requirement.is_expired_without_quotes(),
        }
    if my_quote is None:
        return {}
    responses = list(my_quote.amendment_responses.select_related('amendment').order_by('-created_at'))
    open_response = next((r for r in responses if r.status == AmendmentResponse.PENDING), None)
    return {
        'my_amendment_responses': responses,
        'open_amendment_response': open_response,
        'amendment_accept_form': AmendmentAcceptForm(quote=my_quote) if open_response else None,
    }


def _rfq_conversation_context(request, requirement):
    """The RFQ page's conversation panel (it replaced the old per-supplier
    Q&A, which was the same thing as a message thread). The buyer sees
    every supplier conversation on this RFQ; a manufacturer sees theirs."""
    if request.user.id == requirement.user_id:
        rows = [row for row in services.message_thread_rows(request.user) if row['thread'].requirement_id == requirement.pk]
        return {'rfq_threads': rows}
    supplier = ManufacturerProfile.objects.filter(user=request.user).first()
    if supplier is None:
        return {}
    thread = MessageThread.objects.filter(requirement=requirement, supplier=supplier).first()
    recent = list(thread.messages.select_related('sender').order_by('-created_at')[:5])[::-1] if thread else []
    return {'my_thread': thread, 'my_thread_messages': recent, 'can_start_thread': thread is None and not requirement.is_finished()}


class RFQDeclineView(LoginRequiredMixin, View):
    """Supplier says "not for us". Private to the supplier: the buyer isn't
    notified, the RFQ just leaves this supplier's inbox."""
    http_method_names = ['post']

    def post(self, request, pk):
        requirement = get_object_or_404(Requirement, pk=pk, is_deleted=False)
        supplier = get_object_or_404(ManufacturerProfile, user=request.user)
        if Quote.objects.filter(requirement=requirement, supplier=supplier, is_deleted=False).exists():
            messages.error(request, "You've already quoted on this RFQ — delete your quote instead if you no longer want it.")
            return redirect(reverse('requirement', kwargs={'pk': requirement.pk}))
        RFQDecline.objects.get_or_create(
            requirement=requirement, supplier=supplier,
            defaults={'reason': request.POST.get('reason', '')},
        )
        logger.info("Requirement #%s declined by supplier #%s", requirement.pk, supplier.pk)
        messages.success(request, "RFQ declined.")
        return redirect(reverse('requirement-list'))


class QuoteListView(LoginRequiredMixin, ListView):
    model = Quote
    paginate_by = 10

    def get_template_names(self):
        if self.request.user.role == 'manufacturer':
            return ["quote/quote_list.html"]
        return ["quote/quote_list_buyer.html"]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'manufacturer':
            supplier = ManufacturerProfile.objects.filter(user=user).first()
            return Quote.objects.filter(is_deleted=False, supplier=supplier).select_related('requirement', 'supplier').order_by('-created_at')
        return Quote.objects.filter(
            requirement__user=user, requirement__is_deleted=False, is_deleted=False, is_draft=False,
        ).select_related('requirement', 'supplier').order_by('requirement_id', '-is_selected', 'quote_price')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.role != 'manufacturer':
            grouped = {}
            for quote in context['object_list']:
                grouped.setdefault(quote.requirement, []).append(quote)
            context['quotes_by_requirement'] = grouped
        return context


class QuoteCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Quote
    form_class = QuoteForm
    success_url = '/marketplace/quote'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            requirement = get_object_or_404(Requirement, pk=self.kwargs.get('pk'), is_deleted=False)
            supplier = ManufacturerProfile.objects.filter(user=request.user).first()
            if supplier is None:
                raise PermissionDenied("Only manufacturers can submit quotes.")
            back = reverse('requirement', kwargs={'pk': requirement.pk})
            if not requirement.is_open_for_quotes():
                messages.error(request, "This RFQ is closed to new quotes — it has been awarded or its deadline has passed.")
                return redirect(back)
            if Quote.objects.filter(requirement=requirement, supplier=supplier, is_deleted=False).exists():
                messages.error(request, "You've already quoted on this RFQ. Edit your existing quote instead.")
                return redirect(back)
        return super().dispatch(request, *args, **kwargs)
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
        if requirement:
            context.update(_rfq_conversation_context(self.request, requirement))
        context["is_manufacturer"] = True
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
            quotes = services.annotate_quote_badges(Quote.objects.filter(requirement=requirement, is_deleted=False))
            response = render(request, 'requirement/_quotes_section.html', {
                'demand': requirement,
                'quotes': quotes,
                'is_manufacturer': True,
                'my_quote': quote,
                'selected_quote': next((q for q in quotes if q.is_selected), None),
            })
            response['HX-Redirect'] = reverse('requirement', kwargs={'pk': requirement.pk})
            return response
        return redirect(reverse('requirement', kwargs={'pk': requirement.pk}))


def _own_open_quote(request, pk):
    """The logged-in supplier's own quote, still undecided. Anyone else gets
    a 404; a decided (selected/rejected) quote can't be changed."""
    quote = get_object_or_404(Quote.objects.select_related('supplier', 'requirement'), pk=pk, is_deleted=False)
    if quote.supplier.user_id != request.user.id:
        raise Http404
    if quote.is_selected or quote.status:
        raise PermissionDenied("This quote has already been decided and can no longer be changed.")
    return quote


class QuoteUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Quote
    form_class = QuoteForm
    success_url = '/marketplace/quote'
    success_message = "Quotation details has been updated successfully"
    template_name = "quote/quote_form.html"

    def get_object(self, queryset=None):
        return _own_open_quote(self.request, self.kwargs['pk'])

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
        context.update(_rfq_conversation_context(self.request, requirement))
        context["is_manufacturer"] = True
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        quote = form.save(commit=False)
        quote.is_draft = request.POST.get('action') == 'draft'
        if quote.revision_requested_at and not quote.is_draft:
            # Submitting (not just saving a draft) answers the buyer's request.
            quote.revised_at = timezone.now()
            quote.revision_requested_at = None
            quote.revision_note = ''
            quote.save()
            messages.success(request, "Revised quote sent to the buyer.")
            return redirect(reverse('requirement', kwargs={'pk': quote.requirement.pk}))
        quote.save()
        messages.success(request, "Quote saved as draft." if quote.is_draft else self.success_message)
        return redirect(reverse('requirement', kwargs={'pk': quote.requirement.pk}))


class QuoteDeleteView(LoginRequiredMixin, View):
    template_name = "quote/delete_quote.html"
    success_message = "Quotation has been deleted successfully"

    def get(self, request, pk):
        return render(request, self.template_name, {'object': _own_open_quote(request, pk)})

    def post(self, request, pk):
        quote = _own_open_quote(request, pk)
        quote.is_deleted = True
        quote.save()
        messages.success(request, self.success_message)
        return redirect('quote-list')


class QuoteView(View):
    def get(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        return render(request, 'quote/quote.html', {'quote': quote})


class QuoteStatusUpdateView(LoginRequiredMixin, View):
    """Buyer awards or rejects a quote. POST only (a GET link could be
    triggered by anyone clicking a crafted URL), and only by the buyer who
    owns the RFQ, while both the RFQ and the quote are still undecided."""
    http_method_names = ['post']

    def post(self, request, pk, status):
        quote = get_object_or_404(Quote.objects.select_related('requirement', 'supplier'), pk=pk, is_deleted=False)
        requirement = quote.requirement
        if requirement.user_id != request.user.id or status not in ('Approved', 'Rejected'):
            raise Http404
        if requirement.status or quote.status or quote.is_draft:
            messages.error(request, "This quote can no longer be changed.")
        elif status == 'Approved':
            with transaction.atomic():
                order, rejected = services.award_quote(requirement, quote)
            logger.info(
                "Quote #%s selected for requirement #%s by %s (%d other quote(s) auto-rejected, order #%s)",
                quote.pk, requirement.pk, request.user, rejected, getattr(order, 'billno', None),
            )
            messages.success(request, "Quote awarded. The order has been created.")
        else:
            quote.status = 'Rejected'
            quote.decided_at = timezone.now()
            quote.save()
            logger.info("Quote #%s rejected by %s", quote.pk, request.user)
            messages.success(request, f"Quote from {quote.supplier.companyname or quote.supplier} rejected.")

        if getattr(request, 'htmx', False):
            requirement.refresh_from_db()
            return render(request, 'requirement/_quotes_section.html', quotes_section_context(request, requirement))
        return redirect(reverse('requirement', kwargs={'pk': requirement.id}))


class QuoteRevisionRequestView(LoginRequiredMixin, View):
    """Buyer asks a supplier to revise their quote (price, lead time,
    terms...). The quote stays undecided, so it can still be awarded or
    rejected; the supplier is notified and resubmitting clears the request."""
    http_method_names = ['post']

    def post(self, request, pk):
        quote = get_object_or_404(Quote.objects.select_related('requirement', 'supplier'), pk=pk, is_deleted=False)
        requirement = quote.requirement
        if requirement.user_id != request.user.id:
            raise Http404
        note = request.POST.get('note', '').strip()[:1000]
        if requirement.status or not quote.is_undecided or quote.is_draft:
            messages.error(request, "This quote can no longer be revised.")
        elif quote.awaiting_revision:
            messages.error(request, "You've already asked for a revision of this quote.")
        elif not note:
            messages.error(request, "Tell the supplier what to change.")
        else:
            quote.revision_requested_at = timezone.now()
            quote.revision_note = note
            quote.save()
            logger.info("Revision of quote #%s requested by %s", quote.pk, request.user)
            messages.success(request, f"Revision requested from {quote.supplier.companyname or quote.supplier}.")
        if getattr(request, 'htmx', False):
            return render(request, 'requirement/_quotes_section.html', quotes_section_context(request, requirement))
        return redirect(reverse('requirement', kwargs={'pk': requirement.pk}) + '#quotes-section')


class QuoteRevisionDeclineView(LoginRequiredMixin, View):
    """Supplier keeps their quote as it is instead of revising it. The buyer
    gets no notification; the quote returns to their review pile, labelled
    so it's clear no revision is coming."""
    http_method_names = ['post']

    def post(self, request, pk):
        quote = get_object_or_404(Quote.objects.select_related('supplier', 'requirement'), pk=pk, is_deleted=False)
        if quote.supplier.user_id != request.user.id:
            raise Http404
        if not quote.awaiting_revision:
            messages.error(request, "There's no open revision request on this quote.")
        else:
            quote.revision_requested_at = None
            quote.revision_note = ''
            quote.revision_declined_at = timezone.now()
            quote.save()
            logger.info("Revision request on quote #%s declined by %s", quote.pk, request.user)
            messages.success(request, "Revision request declined. Your original quote stands.")
        return redirect(reverse('quote-list'))


class RequirementStatusUpdateView(LoginRequiredMixin, View):
    """The awarded supplier moves the RFQ into production and then marks it
    completed. POST only, and only the supplier whose quote was selected."""
    http_method_names = ['post']

    def post(self, request, pk, status):
        requirement = get_object_or_404(Requirement, pk=pk, is_deleted=False)
        selected = Quote.objects.filter(requirement=requirement, is_selected=True).select_related('supplier').first()
        if selected is None or selected.supplier.user_id != request.user.id:
            raise Http404

        order = requirement.orders.order_by('-created_at').first()
        if order is None:
            # RFQs awarded before orders were created at award time.
            order = services.create_award_order(requirement, selected)

        if status == 'Production' and requirement.status == 'Approved':
            requirement.status = 'Production'
            requirement.save()
            if order is not None and order.status == 'quote_selected':
                order.start_production(note="Supplier started production")
                order.save()
            messages.success(request, "Production started.")
        elif status == 'Completed' and requirement.status == 'Production':
            if order is not None and order.status == 'quote_selected':
                order.start_production(note="Supplier started production")
                order.save()
            requirement.status = 'Completed'
            requirement.save()
            messages.success(request, "RFQ marked completed.")
        else:
            messages.error(request, "That status change isn't allowed from the RFQ's current state.")
        return redirect(reverse('requirement', kwargs={'pk': requirement.id}))


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
            context = {'bills': orders.order_by('-created_at')}
        else:
            customer = ConsumerProfile.objects.filter(user=user).first()
            orders = Order.objects.filter(customer=customer)
            template_name = "order/order_list.html"
            month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            context = {
                'bills': orders.order_by('-created_at'),
                'stat_in_production': orders.filter(status='in_production').count(),
                'stat_needs_approval': orders.filter(status='payment_pending').count(),
                'stat_in_transit': orders.filter(production_stage='dispatched').exclude(status__in=['completed', 'cancelled']).count(),
                'stat_delivered_this_month': orders.filter(status='completed', updated_at__gte=month_start).count(),
            }
        return render(request, template_name, context)


class OrderDetailView(View):
    template_name = "order/order_detail.html"

    def get(self, request, billno):
        order = get_object_or_404(Order.objects.select_related('supplier', 'customer'), billno=billno)
        if not (request.user.is_staff or request.user in (order.supplier.user, order.customer.user)):
            raise Http404
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
            'qc_items': order.qc_checklist,
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
    def post(self, request, billno, index):
        order = get_object_or_404(Order, billno=billno)
        if request.user != order.supplier.user:
            messages.error(request, "Only the manufacturer on this order can update the QC checklist.")
            return redirect(reverse('order-detail', kwargs={'billno': order.billno}))
        if not order.toggle_qc_item(index, request.user):
            raise Http404
        if getattr(request, 'htmx', False):
            return render(request, 'order/_qc_checklist.html', {'qc_items': order.qc_checklist, 'bill': order, 'is_supplier': True})
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
        order = get_object_or_404(Order.objects.select_related('supplier', 'customer', 'requirement'), billno=billno)
        if request.user not in (order.supplier.user, order.customer.user):
            raise Http404
        transition_name = ORDER_TRANSITIONS.get(status)
        if transition_name and hasattr(order, transition_name):
            transition_method = getattr(order, transition_name)
            try:
                transition_method(note=request.POST.get('note', ''))
                order.save()
                services.sync_after_order_transition(order)
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


RFQ_NUMBER_PATTERN = re.compile(r'^(?:rfq-?)?(\d+)$', re.IGNORECASE)


class global_search_view(LoginRequiredMixin, ListView):
    """Searches only RFQs the user may see: a buyer's own, the open or
    already-quoted ones for a manufacturer, everything for staff. The
    searched fields are fixed; the caller can't choose one (that used to
    allow `user`, which raised a server error)."""
    model = Requirement
    template_name = "globalsearch.html"
    paginate_by = 10

    def _visible_requirements(self):
        user = self.request.user
        if user.is_staff:
            return Requirement.objects.filter(is_deleted=False)
        if user.role == 'manufacturer':
            supplier = ManufacturerProfile.objects.filter(user=user).first()
            if supplier is None:
                return Requirement.objects.none()
            open_ids = services.open_requirements_for(supplier).values('pk')
            return Requirement.objects.filter(Q(pk__in=open_ids) | Q(quote__supplier=supplier), is_deleted=False)
        return Requirement.objects.filter(user=user, is_deleted=False)

    def get_queryset(self):
        query = (self.request.GET.get('search') or '').strip()
        if not query:
            return Requirement.objects.none()
        match = Q(title__icontains=query) | Q(rfq_desc__icontains=query) | Q(industry__icontains=query) \
            | Q(requirement_parts__part_name__icontains=query)
        number = RFQ_NUMBER_PATTERN.match(query)
        if number:
            match |= Q(pk=int(number.group(1)))
        return self._visible_requirements().filter(match).distinct().order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        return context


class DocumentListView(LoginRequiredMixin, View):
    def get(self, request):
        if request.user.role == 'manufacturer':
            # A manufacturer's own uploads (quote files) plus whatever the
            # buyer shared back through production updates on their orders.
            supplier = get_object_or_404(ManufacturerProfile, user=request.user)
            docs = []
            for quote in Quote.objects.filter(supplier=supplier, is_deleted=False).select_related('requirement'):
                if quote.quote_file:
                    docs.append({
                        'name': quote.quote_file.name.rsplit('/', 1)[-1], 'url': quote.quote_file.url, 'type': 'Quote',
                        'linked_label': f'RFQ-{quote.requirement_id}', 'linked_url': reverse('requirement', kwargs={'pk': quote.requirement_id}),
                        'uploaded_by': supplier.companyname or str(supplier), 'date': quote.created_at,
                    })
            for order in Order.objects.filter(supplier=supplier).select_related('requirement'):
                for update in order.updates.select_related('author').all():
                    if update.document:
                        docs.append({
                            'name': update.document.name.rsplit('/', 1)[-1], 'url': update.document.url, 'type': 'Certificate',
                            'linked_label': f'ORD-{order.billno}', 'linked_url': reverse('order-detail', kwargs={'billno': order.billno}),
                            'uploaded_by': update.author.get_full_name() if update.author else '—', 'date': update.created_at,
                        })
            docs.sort(key=lambda doc: doc['date'], reverse=True)
        else:
            docs = services.buyer_documents(request.user)
        docs += services.message_attachment_documents(request.user)
        docs.sort(key=lambda doc: doc['date'], reverse=True)
        return render(request, 'documents/document_list.html', {'docs': docs})


def _safe_next(request, fallback_name='notification-list'):
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return next_url
    return reverse(fallback_name)


class NotificationListView(LoginRequiredMixin, View):
    def get(self, request):
        events = services.notification_feed(request.user)
        return render(request, 'notifications/notification_list.html', {
            'events': events,
            'unread_count': sum(1 for event in events if event['unread']),
        })


class NotificationOpenView(LoginRequiredMixin, View):
    """Clicking a notification marks it read, then follows its link."""
    def get(self, request):
        key = request.GET.get('key', '')[:64]
        if key:
            services.mark_notifications_read(request.user, [key])
        return redirect(_safe_next(request))


class NotificationMarkReadView(LoginRequiredMixin, View):
    def post(self, request):
        key = request.POST.get('key', '')[:64]
        if key:
            services.mark_notifications_read(request.user, [key])
        return redirect(_safe_next(request))


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        keys = [event['key'] for event in services.notification_feed(request.user) if event['unread']]
        services.mark_notifications_read(request.user, keys)
        return redirect(_safe_next(request))


class MessageThreadListView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'messages/thread_list.html', {'thread_rows': services.message_thread_rows(request.user)})


class MessageThreadStartView(LoginRequiredMixin, View):
    """Start (or resume) the conversation between one RFQ's buyer and one
    supplier. The buyer starts it from a quote ("Message"), naming the
    supplier; a manufacturer starts it by asking the buyer a question on an
    RFQ they can quote on (no supplier in the URL: it's always their own).
    An optional `body` becomes the first message."""
    def post(self, request, requirement_pk, supplier_pk=None):
        requirement = get_object_or_404(Requirement, pk=requirement_pk, is_deleted=False)
        back = reverse('requirement', kwargs={'pk': requirement_pk})

        if supplier_pk is None:
            supplier = ManufacturerProfile.objects.filter(user=request.user).first()
            can_see = supplier is not None and (
                services.open_requirements_for(supplier).filter(pk=requirement.pk).exists()
                or Quote.objects.filter(requirement=requirement, supplier=supplier).exists()
                or MessageThread.objects.filter(requirement=requirement, supplier=supplier).exists()
            )
            if not can_see:
                raise Http404
        else:
            if request.user != requirement.user:
                messages.error(request, "Only the buyer who posted this RFQ can start this conversation.")
                return redirect(back)
            supplier = get_object_or_404(ManufacturerProfile, pk=supplier_pk)

        thread = MessageThread.objects.filter(requirement=requirement, supplier=supplier).first()
        if thread is None:
            if requirement.is_finished():
                messages.error(request, "This RFQ is finished, so new conversations can't be started on it.")
                return redirect(back)
            thread = MessageThread.objects.create(requirement=requirement, supplier=supplier)

        body = request.POST.get('body', '').strip()
        if body and not thread.is_closed:
            Message.objects.create(thread=thread, sender=request.user, body=body[:2000])
        return redirect(reverse('message-thread', kwargs={'pk': thread.pk}))


def _get_participant_thread(request, pk):
    thread = get_object_or_404(MessageThread.objects.select_related('requirement__user', 'supplier__user'), pk=pk)
    if not thread.is_participant(request.user):
        raise Http404
    return thread


def _thread_messages_context(thread, form=None):
    return {
        'thread': thread,
        'thread_messages': thread.messages.select_related('sender').all(),
        'message_form': form or MessageForm(),
        'message_sig': services.thread_signature(thread),
    }


def _thread_response(request, thread, form=None):
    """htmx requests get just the message pane re-rendered; plain form posts redirect back."""
    if getattr(request, 'htmx', False):
        return render(request, 'messages/_thread_messages.html', _thread_messages_context(thread, form))
    return redirect(reverse('message-thread', kwargs={'pk': thread.pk}))


class MessageThreadDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        thread = _get_participant_thread(request, pk)
        thread.mark_read_for(request.user)
        services.invalidate_notification_cache(request.user)
        context = _thread_messages_context(thread)
        context['sidebar_thread_rows'] = services.message_thread_rows(request.user)
        return render(request, 'messages/thread_detail.html', context)

    def post(self, request, pk):
        thread = _get_participant_thread(request, pk)
        if thread.is_closed:
            messages.error(request, "This conversation is closed.")
            return _thread_response(request, thread)
        form = MessageForm(request.POST, request.FILES)
        if not form.is_valid():
            if getattr(request, 'htmx', False):
                return _thread_response(request, thread, form)
            for error in form.errors.values():
                messages.error(request, error.as_text().lstrip('* '))
            return _thread_response(request, thread)
        message = form.save(commit=False)
        message.thread = thread
        message.sender = request.user
        message.save()
        return _thread_response(request, thread)


class MessageDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        message = get_object_or_404(Message.objects.select_related('thread__requirement', 'thread__supplier'), pk=pk)
        thread = message.thread
        if not thread.is_participant(request.user):
            raise Http404
        if message.sender_id != request.user.id:
            messages.error(request, "You can only delete your own messages.")
        elif thread.is_closed:
            messages.error(request, "Messages in a closed conversation can't be deleted.")
        elif not message.is_deleted:
            message.soft_delete()
            logger.info("Message #%s deleted by %s", message.pk, request.user)
        return _thread_response(request, thread)


class MessageAttachmentDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        message = get_object_or_404(Message.objects.select_related('thread__requirement', 'thread__supplier'), pk=pk)
        if not message.thread.is_participant(request.user) or not message.has_attachment:
            raise Http404
        return FileResponse(
            message.attachment.open('rb'),
            as_attachment=True,
            filename=message.attachment_name or 'attachment',
            content_type=message.attachment_content_type or 'application/octet-stream',
        )


class MessageThreadCloseView(LoginRequiredMixin, View):
    def post(self, request, pk):
        thread = _get_participant_thread(request, pk)
        if thread.can_be_closed():
            thread.close(request.user)
            messages.success(request, "Conversation closed.")
        elif not thread.is_closed:
            messages.error(request, "A conversation can only be closed once its RFQ is completed or its order is finished.")
        return redirect(reverse('message-thread', kwargs={'pk': thread.pk}))


# --- Live updates (htmx polling) ---------------------------------------------
# Each poller sends back the signature of what it currently shows; an
# unchanged signature gets an empty 204 (htmx leaves the page alone), and a
# logged-out poller gets 286, which tells htmx to stop polling. These views
# are exempt from LoginRequiredMiddleware (see urls.py) so a lapsed session
# stops the poll instead of swapping the login page into the bell.

NO_CHANGE = 204
STOP_POLLING = 286


def _poll_response(status):
    return HttpResponse(status=status)


def quotes_section_context(request, requirement):
    """Everything _quotes_section.html needs, for the RFQ page, htmx swaps and the quotes poll."""
    quotes = services.annotate_quote_badges(
        Quote.objects.filter(requirement=requirement, is_deleted=False).select_related('supplier', 'supplier__company')
    )
    is_manufacturer = request.user.role == 'manufacturer'
    my_quote = None
    my_declined = False
    if is_manufacturer:
        supplier = ManufacturerProfile.objects.filter(user=request.user).first()
        my_quote = next((q for q in quotes if q.supplier_id == getattr(supplier, 'pk', None)), None)
        my_declined = supplier is not None and RFQDecline.objects.filter(requirement=requirement, supplier=supplier).exists()
    return {
        'demand': requirement,
        'quotes': quotes,
        'is_manufacturer': is_manufacturer,
        'is_buyer': request.user.id == requirement.user_id,
        'my_quote': my_quote,
        'my_declined': my_declined,
        'selected_quote': next((q for q in quotes if q.is_selected), None),
        'quotes_sig': services.quotes_signature(requirement),
    }


class LiveTopbarView(View):
    """Keeps the notification bell and sidebar badges current on every dashboard page."""
    def get(self, request):
        if not request.user.is_authenticated:
            return _poll_response(STOP_POLLING)
        from homepage.context_processors import dashboard_sidebar_counts
        context = dashboard_sidebar_counts(request)
        if not context or request.GET.get('sig') == context['live_sig']:
            return _poll_response(NO_CHANGE)
        context['next_url'] = request.htmx.current_url_abs_path if getattr(request, 'htmx', False) else reverse('home')
        return render(request, 'live/_topbar_update.html', context)


class MessageThreadPollView(View):
    """New messages in an open conversation, without touching the reply box."""
    def get(self, request, pk):
        if not request.user.is_authenticated:
            return _poll_response(STOP_POLLING)
        thread = _get_participant_thread(request, pk)
        if thread.is_closed and request.GET.get('closed') != '1':
            # Closing also hides the reply box, which lives outside the polled list.
            response = _poll_response(200)
            response['HX-Refresh'] = 'true'
            return response
        sig = services.thread_signature(thread)
        if request.GET.get('sig') == sig:
            return _poll_response(NO_CHANGE)
        thread.mark_read_for(request.user)
        services.invalidate_notification_cache(request.user)
        context = _thread_messages_context(thread)
        return render(request, 'messages/_message_list.html', context)


class QuotesSectionPollView(View):
    """New or changed quotes on an open RFQ."""
    def get(self, request, pk):
        if not request.user.is_authenticated:
            return _poll_response(STOP_POLLING)
        requirement = get_object_or_404(Requirement, pk=pk, is_deleted=False)
        if request.user.role != 'manufacturer' and request.user.id != requirement.user_id:
            raise Http404
        if request.GET.get('sig') == services.quotes_signature(requirement):
            return _poll_response(NO_CHANGE)
        return render(request, 'requirement/_quotes_section.html', quotes_section_context(request, requirement))
