import datetime
import logging
import csv
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core import session_security
from django.utils.dateparse import parse_date
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

from accounts import team
from accounts.models import ManufacturerProfile

from plans import access, export, rfq_inbox
from plans import catalog as plan_catalog

from . import approvals, documents, emails, reports, search, services
from .models import (
    APPROVAL_APPROVED, ApprovalRequest,
    Requirement, RequirementPart, Quote, QuoteRevision, QuoteTemplate, RFQAlertPreference, Order,
    RFQDecline, RequirementNDAAcceptance,
    MessageThread, Message,
    RequirementAmendment, AmendmentResponse, SupplierReview, OrderDocument,
    GST_EXPORT_LUT, GST_LABELS, GST_RATE, gst_treatment,
)
from .forms import (
    SelectRequirement, RequirementPartInlineFormSet, QuoteForm,
    ShipmentForm, ProductionUpdateForm, MessageForm, AmendmentAcceptForm,
    SupplierReviewForm,
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

# Which side of the deal may trigger each transition. A target absent here
# (quoted, quote_selected) is left open to either party: in the normal flow
# both fire together, synchronously, inside services.create_award_order
# rather than through this view, so this only guards a legacy path for
# orders that predate that (see RequirementStatusUpdateView). 'cancelled'
# isn't listed — _may_perform_transition below handles it separately,
# since it depends on the order's current state rather than a fixed role.
ORDER_TRANSITION_ROLE = {
    'in_production': 'supplier',      # supplier starts the work
    'payment_pending': 'supplier',    # supplier asks to be paid
    'paid': 'buyer',                  # buyer confirms payment (until a payment gateway drives this instead)
    'completed': 'buyer',             # buyer confirms the order is done — this is also what unlocks their rating
}

# Cancelling unilaterally is only safe before the supplier has started
# spending time and material on the order. There's no mutual-agreement
# flow yet for cancelling a later-stage order — that needs both sides'
# sign-off, which isn't built, so it's simply blocked here for now.
CANCELLABLE_ORDER_STATUSES = {'submitted', 'quoted', 'quote_selected'}


def _order_role(order, user):
    """'supplier' or 'buyer' when the user is on that party's company
    account, else None."""
    supplier = team.supplier_profile(user)
    if supplier is not None and supplier.pk == order.supplier_id:
        return 'supplier'
    customer = team.buyer_profile(user)
    if customer is not None and customer.pk == order.customer_id:
        return 'buyer'
    return None


APPROVERS = "your company's owner or an admin"
VIEW_ONLY = "Your role on this company account can't do that."

# The role permission (accounts.team.ROLE_PERMISSIONS) each order
# transition needs, on whichever side may make it (ORDER_TRANSITION_ROLE).
ORDER_TRANSITION_ACTION = {
    'in_production': 'order.production',
    'payment_pending': 'invoice.manage',
    'paid': 'order.confirm_payment',
    'completed': 'order.manage',
    'cancelled': 'order.manage',
}


def _acceptance(user):
    """How `user` may accept a quotation: ('direct', ''), ('approval', '')
    — a request an owner or admin decides — or (None, why not)."""
    if team.allows(user, 'quote.accept'):
        return 'direct', ''
    if access.can(user, 'quote.request_accept'):
        return 'approval', ''
    if team.allows(user, 'quote.request_accept'):
        return None, ("Only your company's owner or an admin can accept a quotation on your plan. "
                      "Asking them to approve it is part of the Business plan.")
    return None, "Only your company's owner or an admin can accept a quotation."


def _may_perform_transition(order, user, status):
    """Whether `user` — already confirmed to be a party on the order — may
    trigger the transition that reaches `status`. Without this, either
    side could press any next-step button: a supplier marking their own
    order paid and completed, or a buyer starting production themselves."""
    role = _order_role(order, user)
    if role is None or not team.allows(user, ORDER_TRANSITION_ACTION.get(status, 'order.manage')):
        return False
    if status == 'cancelled':
        return order.status in CANCELLABLE_ORDER_STATUSES
    required_role = ORDER_TRANSITION_ROLE.get(status)
    return required_role is None or required_role == role


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
            supplier = team.supplier_profile(user)
            requirement = Requirement.objects.filter(
                is_deleted=False, status=status, quote__supplier=supplier, quote__is_selected=True
            )
        else:
            requirement = Requirement.objects.filter(is_deleted=False, status=status, user_id__in=team.team_user_ids(user))
        return requirement


class RequirementListView(LoginRequiredMixin, ListView):
    model = Requirement
    paginate_by = 10

    def get_template_names(self):
        if self.request.user.role == 'manufacturer':
            return ["requirement/rfq_inbox.html"]
        return ["requirement/requirement_list.html"]

    def get(self, request, *args, **kwargs):
        if request.GET.get('export') == 'csv':
            refused = _export_refused(request)
            if refused:
                return refused
            rows = self.get_queryset()
            return export.csv_response('rfqs', ['RFQ', 'Title', 'Parts', 'Currency', 'Due', 'Status', 'Created'], (
                [f"RFQ-{r.pk}", r.title, r.parts, r.quote_currency, r.end_date.strftime('%Y-%m-%d') if r.end_date else '',
                 r.get_status_display() if r.status else 'Open', r.created_at.strftime('%Y-%m-%d')]
                for r in rows
            ))
        return super().get(request, *args, **kwargs)

    def _manufacturer_supplier(self):
        return team.supplier_profile(self.request.user)

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
                    rfq_inbox.deliver(supplier)
                    queryset = rfq_inbox.received(queryset, supplier)
                    queryset = queryset.exclude(quote__supplier=supplier).exclude(declines__supplier=supplier)
            process = self.request.GET.get('process', '')
            if process:
                queryset = queryset.filter(requirement_parts__technology=process).distinct()
        else:
            queryset = Requirement.objects.filter(user_id__in=team.team_user_ids(user), is_deleted=False)
            tab = self.request.GET.get('tab', 'all')
            if tab == 'approval':
                queryset = queryset.exclude(approval_status=APPROVAL_APPROVED)
            elif tab == 'collecting':
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
        context['can_export'] = access.can(user, 'export')
        if user.role == 'manufacturer':
            supplier = self._manufacturer_supplier()
            context['tab'] = self.request.GET.get('tab', 'new')
            context['process'] = self.request.GET.get('process', '')
            context['tab_counts'] = {'new': 0, 'quoted': 0, 'won': 0, 'lost': 0}
            if supplier:
                context['rfqs_waiting'] = rfq_inbox.waiting_count(supplier)
                context['rfq_limit'] = access.limit(supplier, plan_catalog.RFQS_RECEIVED_PER_MONTH)
                context['show_match'] = access.can(user, 'rfq.match_scores')
                context['tab_counts'] = {
                    'new': rfq_inbox.received(services.open_requirements_for(supplier), supplier).exclude(
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
            own = Requirement.objects.filter(user_id__in=team.team_user_ids(user), is_deleted=False)
            context['tab'] = self.request.GET.get('tab', 'all')
            context['q'] = self.request.GET.get('q', '')
            context['tab_counts'] = {
                'all': own.count(),
                'approval': own.exclude(approval_status=APPROVAL_APPROVED).count(),
                'collecting': own.filter(status__isnull=True, quote__isnull=True).count(),
                'ready': own.filter(status__isnull=True, quote__isnull=False).distinct().count(),
                'awarded': own.filter(status__in=['Approved', 'Production', 'Completed']).count(),
            }
        return context


def _gst_context(requirement, supplier):
    """GST rate and label for the quote form's live total (0% for an
    export when the manufacturer has a valid LUT)."""
    treatment = gst_treatment(requirement, supplier)
    return {
        'gst_rate': '0' if treatment == GST_EXPORT_LUT else str(GST_RATE),
        'gst_label': GST_LABELS[treatment],
    }


def _own_open_requirement(request, pk):
    """An RFQ of the logged-in buyer's company, still open for editing (not
    yet awarded). Anyone outside the company gets a 404, so RFQ ids can't be
    probed; a Viewer can't change it."""
    requirement = get_object_or_404(Requirement, pk=pk, is_deleted=False)
    if not team.is_teammate(request.user, requirement.user_id):
        raise Http404
    if not team.allows(request.user, 'rfq.edit'):
        raise PermissionDenied(VIEW_ONLY)
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
            if not team.allows(request.user, 'rfq.create'):
                raise PermissionDenied(VIEW_ONLY)
            decision = access.check(request.user, 'rfq.create')
            if not decision:
                messages.error(request, decision.reason)
                # ?upgrade=1 opens the plan modal in the dashboard's subscription widget.
                return redirect(reverse('home') + '?upgrade=1')
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
                team.log(request.user, 'rfq.created', f"Posted RFQ-{requirement.pk}: {requirement.title}",
                         reverse('requirement', kwargs={'pk': requirement.pk}))
                emails.notify_new_rfq(requirement)
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
            messages.success(request, self._settle_approval(request) or self.success_message)
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


    def _settle_approval(self, request):
        """An RFQ still waiting for (or refused) approval from before RFQs
        stopped needing it: saving it releases it to suppliers."""
        requirement = self.object
        if requirement.approval_status == APPROVAL_APPROVED:
            return None
        url = reverse('requirement', kwargs={'pk': requirement.pk})
        approvals.cancel_open(f"Sent to suppliers directly by {request.user.get_full_name() or request.user.email}.",
                              requirement=requirement, kind=ApprovalRequest.RFQ)
        Requirement.objects.filter(pk=requirement.pk).update(approval_status=APPROVAL_APPROVED)
        requirement.approval_status = APPROVAL_APPROVED
        team.log(request.user, 'rfq.created', f"Sent RFQ-{requirement.pk} to suppliers: {requirement.title}", url)
        emails.notify_new_rfq(requirement)
        return "RFQ saved and sent to suppliers."


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
        if not team.is_teammate(request.user, amendment.requirement.user_id):
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
        supplier = team.supplier_profile(request.user)
        if supplier is None or response.quote.supplier_id != supplier.pk:
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
        if not team.is_teammate(request.user, requirement.user_id):
            raise Http404
        back = reverse('requirement', kwargs={'pk': requirement.pk}) + '#changes'
        if (response.status != AmendmentResponse.ACCEPTED or amendment.status != RequirementAmendment.PENDING
                or requirement.status or not response.quote.is_undecided):
            messages.error(request, "This pricing can no longer be accepted.")
            return redirect(back)

        supplier_name = response.quote.supplier.companyname or response.quote.supplier
        if not team.allows(request.user, 'quotes.manage'):
            raise PermissionDenied(VIEW_ONLY)
        how, why_not = _acceptance(request.user) if request.POST.get('decision') == 'accept' else (None, '')
        if request.POST.get('decision') == 'accept' and how is None:
            messages.error(request, why_not)
        elif request.POST.get('decision') == 'accept' and how == 'approval':
            approvals.submit(
                request.user, ApprovalRequest.AWARD, requirement=requirement, quote=response.quote,
                payload={'amendment_response': response.pk},
            )
            messages.success(request, f"Accepting {supplier_name}'s new pricing (which awards them the RFQ) was sent to {APPROVERS} for approval.")
        elif request.POST.get('decision') == 'accept':
            with transaction.atomic():
                order, rejected = services.accept_amendment_pricing(response)
                approvals.cancel_open("The RFQ was awarded.", requirement=requirement, kind=ApprovalRequest.AWARD)
            team.log(request.user, 'award.done', f"Awarded RFQ-{requirement.pk} to {supplier_name} at their new pricing",
                     reverse('requirement', kwargs={'pk': requirement.pk}))
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
        approvals.cancel_open("The RFQ was deleted.", requirement=requirement)
        team.log(request.user, 'rfq.deleted', f"Deleted RFQ-{requirement.pk}: {requirement.title}")
        messages.success(request, self.success_message)
        return redirect('requirement-list')


class RequirementView(LoginRequiredMixin, View):
    def get(self, request, pk):
        # Staff keep full access, including a deleted RFQ, for support.
        # Everyone else gets exactly the scope search and the RFQ
        # list/inbox already use: a buyer's own RFQs, or a manufacturer's
        # open-to-quote and already-quoted ones — never deleted, never
        # someone else's awarded RFQ. Without this, the id in the URL was
        # the only thing standing between any manufacturer and any RFQ.
        if request.user.is_staff:
            requirement = get_object_or_404(Requirement, pk=pk)
        else:
            requirement = get_object_or_404(services.visible_requirements_for(request.user), pk=pk)
        context = quotes_section_context(request, requirement)
        # NDA'd RFQs: the buyer and staff always see everything; a
        # manufacturer sees the summary only until they've accepted.
        supplier = None
        if not request.user.is_staff and not context['is_buyer']:
            supplier = team.supplier_profile(request.user)
            locked = _rfq_quota_gate(request, supplier, requirement)
            if locked is not None:
                return locked
        nda_accepted = request.user.is_staff or context['is_buyer'] or requirement.nda_accepted_by(supplier)
        context['nda_accepted'] = nda_accepted
        context['demanddetails'] = RequirementPart.objects.filter(requirement=requirement).all() if nda_accepted else RequirementPart.objects.none()
        context.update(_rfq_conversation_context(request, requirement))
        context.update(_change_request_context(request, requirement, context.get('my_quote')))
        return render(request, 'requirement/requirement.html', context)


def _rfq_quota_gate(request, supplier, requirement):
    """None when `supplier` may open `requirement` (already received, quoted
    on, or this month's plan has room to receive it — plans.rfq_inbox);
    otherwise the upgrade page."""
    if supplier is None or not requirement.is_open_for_quotes():
        return None
    if Quote.objects.filter(requirement=requirement, supplier=supplier).exists() or rfq_inbox.receive(supplier, requirement):
        return None
    limit = access.limit(supplier, plan_catalog.RFQS_RECEIVED_PER_MONTH)
    return render(request, 'requirement/rfq_locked.html', {
        'demand': requirement, 'limit': limit, 'plan_label': plan_catalog.PLAN_LABELS[access.plan_of(supplier)],
    }, status=402)


class RequirementNDAAcceptView(LoginRequiredMixin, View):
    """A manufacturer accepts an NDA'd RFQ's terms before its master file
    and part details become visible to them. Recorded once per supplier
    per RFQ; the buyer never sees or triggers this."""
    http_method_names = ['post']

    def post(self, request, pk):
        requirement = get_object_or_404(services.visible_requirements_for(request.user), pk=pk, nda_required=True)
        supplier = team.supplier_profile(request.user)
        if supplier is None:
            raise Http404
        RequirementNDAAcceptance.objects.get_or_create(requirement=requirement, supplier=supplier)
        return redirect(reverse('requirement', kwargs={'pk': requirement.pk}))


def _change_request_context(request, requirement, my_quote):
    """Change requests for the RFQ page: the buyer sees every request with
    every supplier's response; a supplier sees only their own responses."""
    if team.is_teammate(request.user, requirement.user_id):
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
    if team.is_teammate(request.user, requirement.user_id):
        rows = [row for row in services.message_thread_rows(request.user) if row['thread'].requirement_id == requirement.pk]
        return {'rfq_threads': rows}
    supplier = team.supplier_profile(request.user)
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
        supplier = team.supplier_profile(request.user)
        if supplier is None:
            raise Http404
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

    QUOTE_FILTERS = {
        'draft': {'is_draft': True},
        'sent': {'is_draft': False, 'is_selected': False, 'status__isnull': True},
        'won': {'is_selected': True},
        'lost': {'status': 'Rejected'},
    }

    def get(self, request, *args, **kwargs):
        if request.GET.get('export') == 'csv':
            refused = _export_refused(request)
            if refused:
                return refused
            return export.csv_response('quotes', ['Quote', 'RFQ', 'RFQ title', 'Supplier', 'Unit price', 'Tooling', 'Lead time', 'Payment terms', 'Status', 'Submitted'], (
                [q.pk, f"RFQ-{q.requirement_id}", q.requirement.title, q.supplier.companyname or q.supplier, q.quote_price,
                 q.tooling_cost, f"{q.lead_time_value or ''} {q.lead_time_unit if q.lead_time_value else ''}".strip(),
                 q.get_payment_terms_display(), 'Draft' if q.is_draft else ('Won' if q.is_selected else (q.status or 'Sent')),
                 q.submitted_at.strftime('%Y-%m-%d') if q.submitted_at else '']
                for q in self.get_queryset()
            ))
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        if user.role == 'manufacturer':
            supplier = team.supplier_profile(user)
            queryset = Quote.objects.filter(is_deleted=False, supplier=supplier).select_related('requirement', 'supplier').order_by('-created_at')
            status = self.request.GET.get('status', '')
            search = self.request.GET.get('q', '').strip()
            if access.can(user, 'quotes.filter'):
                if status in self.QUOTE_FILTERS:
                    queryset = queryset.filter(**self.QUOTE_FILTERS[status])
                if search:
                    queryset = queryset.filter(requirement__title__icontains=search)
            return queryset
        return Quote.objects.filter(
            requirement__user_id__in=team.team_user_ids(user), requirement__is_deleted=False, is_deleted=False, is_draft=False,
        ).select_related('requirement', 'supplier').order_by('requirement_id', '-is_selected', 'quote_price')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['can_export'] = access.can(user, 'export')
        if user.role == 'manufacturer':
            context['can_filter_quotes'] = access.can(user, 'quotes.filter')
            context['can_bulk_quotes'] = access.can(user, 'quotes.bulk')
            context['status_filter'] = self.request.GET.get('status', '')
            context['q'] = self.request.GET.get('q', '').strip()
        if user.role != 'manufacturer':
            grouped = {}
            for quote in context['object_list']:
                grouped.setdefault(quote.requirement, []).append(quote)
            context['quotes_by_requirement'] = grouped
        return context


class QuoteBulkView(LoginRequiredMixin, View):
    """Withdraws several of the company's draft quotes at once (supplier
    plans with advanced quote management)."""
    http_method_names = ['post']

    def post(self, request):
        decision = access.check(request.user, 'quotes.bulk')
        supplier = team.supplier_profile(request.user)
        if not decision or supplier is None:
            messages.error(request, decision.reason or "Only manufacturers can do that.")
            return redirect(reverse('quote-list'))
        ids = [int(pk) for pk in request.POST.getlist('quote') if pk.isdigit()]
        drafts = Quote.objects.filter(supplier=supplier, pk__in=ids, is_draft=True, is_deleted=False)
        count = drafts.update(is_deleted=True)
        team.log(request.user, 'quote.bulk_withdrawn', f"Withdrew {count} draft quote{'s' if count != 1 else ''}", reverse('quote-list'))
        messages.success(request, f"Withdrew {count} draft quote{'s' if count != 1 else ''}.")
        return redirect(reverse('quote-list'))


class QuoteTemplateDeleteView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        supplier = team.supplier_profile(request.user)
        template = get_object_or_404(QuoteTemplate, pk=pk, supplier=supplier) if supplier else None
        if template is None:
            raise Http404
        template.delete()
        messages.success(request, f"Deleted the template \"{template.name}\".")
        return redirect(_safe_next(request, 'quote-list'))


class QuoteCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Quote
    form_class = QuoteForm
    success_url = '/marketplace/quote'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            requirement = get_object_or_404(Requirement, pk=self.kwargs.get('pk'), is_deleted=False)
            supplier = team.supplier_profile(request.user)
            if supplier is None:
                raise PermissionDenied("Only manufacturers can submit quotes.")
            if not team.allows(request.user, 'quotes.manage'):
                raise PermissionDenied(VIEW_ONLY)
            locked = _rfq_quota_gate(request, supplier, requirement)
            if locked is not None:
                return locked
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
        supplier = team.supplier_profile(self.request.user)
        context["match_percent"] = services.compute_match_percent(requirement, supplier) if (requirement and supplier) else None
        # Same NDA gate as the RFQ page: without it, this form was a second
        # way to the master file, part notes and drawings.
        context["nda_accepted"] = requirement.nda_accepted_by(supplier) if requirement else True
        if requirement:
            context.update(_rfq_conversation_context(self.request, requirement))
            context.update(_gst_context(requirement, supplier))
        context["is_manufacturer"] = True
        context.update(_quote_plan_context(self.request, supplier))
        return context

    def get_initial(self):
        initial = super().get_initial()
        supplier = team.supplier_profile(self.request.user)
        template_id = self.request.GET.get('template', '')
        if supplier and template_id.isdigit() and access.can(self.request.user, 'quote.templates'):
            template = QuoteTemplate.objects.filter(supplier=supplier, pk=int(template_id)).first()
            if template:
                initial.update(template.initial())
        return initial

    def post(self, request, *args, **kwargs):
        # Fixed bug: this view used to bypass form validation entirely,
        # hand-building a Quote from raw POST data with no validation on
        # quote_price. `requirement`/`supplier` are assigned here from the
        # URL/session — never taken from the form — so a manufacturer can't
        # submit a quote as someone else or against a requirement they
        # didn't open.
        self.object = None
        requirement = get_object_or_404(Requirement, pk=self.kwargs.get('pk'))
        supplier_details = team.supplier_profile(request.user)
        if supplier_details is None:
            raise Http404
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        quote = form.save(commit=False)
        quote.requirement = requirement
        quote.supplier = supplier_details
        quote.created_by = request.user
        submitting = request.POST.get('action') != 'draft'
        submitting = _may_submit_quote(request, submitting)
        quote.is_draft = not submitting
        if submitting:
            quote.submitted_at = timezone.now()
        quote.save()
        _save_as_template(request, supplier_details, quote)
        logger.info("Quote #%s saved for requirement #%s by %s", quote.pk, requirement.pk, request.user)
        if submitting:
            emails.notify_quote_submitted(quote)
            team.log(request.user, 'quote.submitted', f"Quoted on RFQ-{requirement.pk}: {requirement.title}",
                     reverse('requirement', kwargs={'pk': requirement.pk}))
            messages.success(request, self.success_message)
        else:
            messages.success(request, "Quote saved as draft.")
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
    """A quote of the logged-in supplier's company, still undecided. Anyone
    outside the company gets a 404; a Viewer can't change it; a decided
    (selected/rejected) quote can't be changed."""
    quote = get_object_or_404(Quote.objects.select_related('supplier', 'requirement'), pk=pk, is_deleted=False)
    supplier = team.supplier_profile(request.user)
    if supplier is None or quote.supplier_id != supplier.pk:
        raise Http404
    if not team.allows(request.user, 'quotes.manage'):
        raise PermissionDenied(VIEW_ONLY)
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
        context["nda_accepted"] = requirement.nda_accepted_by(self.object.supplier)
        context.update(_rfq_conversation_context(self.request, requirement))
        context.update(_gst_context(requirement, self.object.supplier))
        context["is_manufacturer"] = True
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        was_draft = self.object.is_draft  # captured before form binding overwrites it below
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        submitting = request.POST.get('action') != 'draft'
        if was_draft and submitting:
            submitting = _may_submit_quote(request, submitting)
        back = reverse('requirement', kwargs={'pk': self.object.requirement_id})
        if not was_draft and form.has_changed():
            # The buyer has seen the current version: keep it in the history.
            QuoteRevision.snapshot(self.object, request.user)
        quote = form.save(commit=False)
        quote.is_draft = not submitting
        if submitting and quote.submitted_at is None:
            quote.submitted_at = timezone.now()
        if submitting:
            quote.approval_status = APPROVAL_APPROVED
            approvals.cancel_open(f"Sent directly by {request.user.get_full_name() or request.user.email}.",
                                  quote=quote, kind=ApprovalRequest.QUOTE)
            team.log(request.user, 'quote.submitted', f"Sent quote on RFQ-{quote.requirement_id}: {quote.requirement.title}", back)
        if quote.revision_requested_at and not quote.is_draft:
            # Submitting (not just saving a draft) answers the buyer's request.
            quote.revised_at = timezone.now()
            quote.revision_requested_at = None
            quote.revision_note = ''
            quote.save()
            emails.notify_quote_submitted(quote)
            messages.success(request, "Revised quote sent to the buyer.")
            return redirect(reverse('requirement', kwargs={'pk': quote.requirement.pk}))
        quote.save()
        if was_draft and not quote.is_draft:
            # A draft going out for the first time — same event as a
            # fresh submission, from the buyer's side.
            emails.notify_quote_submitted(quote)
        messages.success(request, "Quote saved as draft." if quote.is_draft else self.success_message)
        return redirect(reverse('requirement', kwargs={'pk': quote.requirement.pk}))


def _upgrade_page(request, decision, title):
    """The page shown instead of a feature the plan doesn't include (or the
    role can't use)."""
    return render(request, 'plans/upgrade_required.html', {
        'title': title, 'decision': decision, 'can_change_plan': team.can_manage_subscription(request.user),
    }, status=403 if decision.upgrade_to == '' else 402)


def _export_refused(request):
    """None if this user may export (plans with Excel/CSV export), else a
    redirect back with the reason."""
    decision = access.check(request.user, 'export')
    if decision:
        return None
    messages.error(request, "Excel / CSV export: " + decision.reason)
    return redirect(request.path)


def _may_submit_quote(request, submitting):
    """Whether a quote being sent may go out under the plan's quotes-per-month
    limit. If not, it's kept as a draft and the user is told why."""
    if not submitting:
        return False
    decision = access.check(request.user, 'quote.submit')
    if decision:
        return True
    messages.warning(request, decision.reason + " Your quote was saved as a draft.")
    return False


def _save_as_template(request, supplier, quote):
    """"Save these terms as a template" on the quote form (Starter+)."""
    name = request.POST.get('save_as_template', '').strip()[:100]
    if not name or not access.can(request.user, 'quote.templates'):
        return
    QuoteTemplate.objects.update_or_create(supplier=supplier, name=name, defaults={
        'tooling_cost': quote.tooling_cost, 'lead_time_value': quote.lead_time_value,
        'lead_time_unit': quote.lead_time_unit, 'payment_terms': quote.payment_terms or '',
        'valid_for_days': (quote.valid_until - timezone.localdate()).days if quote.valid_until and quote.valid_until > timezone.localdate() else None,
        'note': quote.note or '', 'created_by': request.user,
    })
    messages.info(request, f"Saved the terms as the template \"{name}\".")


def _quote_plan_context(request, supplier):
    """What the quote form can offer on this plan."""
    can_template = access.can(request.user, 'quote.templates')
    return {
        'can_use_templates': can_template,
        'quote_templates': QuoteTemplate.objects.filter(supplier=supplier) if (supplier and can_template) else [],
        'quotes_left': access.remaining(supplier, plan_catalog.QUOTES_PER_MONTH) if supplier else None,
    }


class QuoteDeleteView(LoginRequiredMixin, View):
    template_name = "quote/delete_quote.html"
    success_message = "Quotation has been deleted successfully"

    def get(self, request, pk):
        return render(request, self.template_name, {'object': _own_open_quote(request, pk)})

    def post(self, request, pk):
        quote = _own_open_quote(request, pk)
        quote.is_deleted = True
        quote.save()
        approvals.cancel_open("The quote was deleted.", quote=quote)
        team.log(request.user, 'quote.deleted', f"Withdrew quote on RFQ-{quote.requirement_id}: {quote.requirement.title}")
        messages.success(request, self.success_message)
        return redirect('quote-list')


class QuoteView(View):
    """The full quote, prices included — private to the RFQ's buyer (once
    the quote isn't a draft), the quote's own supplier, and staff. Quote
    ids are sequential, so without this check any signed-in user could walk
    every quote on every RFQ, including a rival's price."""
    def get(self, request, pk):
        quote = get_object_or_404(Quote.objects.select_related('requirement', 'supplier'), pk=pk, is_deleted=False)
        is_buyer = team.is_teammate(request.user, quote.requirement.user_id) and not quote.is_draft
        own_supplier = team.supplier_profile(request.user)
        is_own_supplier = own_supplier is not None and own_supplier.pk == quote.supplier_id
        if not (is_buyer or is_own_supplier or request.user.is_staff):
            raise Http404
        return render(request, 'quote/quote.html', {'quote': quote})


class QuoteStatusUpdateView(LoginRequiredMixin, View):
    """Buyer awards or rejects a quote. POST only (a GET link could be
    triggered by anyone clicking a crafted URL), and only by the buyer who
    owns the RFQ, while both the RFQ and the quote are still undecided."""
    http_method_names = ['post']

    def post(self, request, pk, status):
        quote = get_object_or_404(Quote.objects.select_related('requirement', 'supplier'), pk=pk, is_deleted=False)
        requirement = quote.requirement
        if not team.is_teammate(request.user, requirement.user_id) or status not in ('Approved', 'Rejected'):
            raise Http404
        if not team.allows(request.user, 'quotes.manage'):
            raise PermissionDenied(VIEW_ONLY)
        how, why_not = _acceptance(request.user) if status == 'Approved' else (None, '')
        if status == 'Approved' and how is None:
            messages.error(request, why_not)
            return redirect(reverse('requirement', kwargs={'pk': requirement.pk}))
        award_needs_approval = how == 'approval'
        if status == 'Approved' and not award_needs_approval:
            # Awarding creates the order and issues a purchase order: a
            # stolen or shared session shouldn't be enough on its own.
            reauth = session_security.require_recent_auth(request, reverse('requirement', kwargs={'pk': requirement.pk}))
            if reauth is not None:
                return reauth
        supplier_name = quote.supplier.companyname or quote.supplier
        if requirement.status or quote.status or quote.is_draft:
            messages.error(request, "This quote can no longer be changed.")
        elif award_needs_approval:
            approvals.submit(request.user, ApprovalRequest.AWARD, requirement=requirement, quote=quote)
            messages.success(request, f"Award to {supplier_name} sent to {APPROVERS} for approval.")
        elif status == 'Approved':
            with transaction.atomic():
                order, rejected = services.award_quote(requirement, quote)
                approvals.cancel_open("The RFQ was awarded.", requirement=requirement, kind=ApprovalRequest.AWARD)
            team.log(request.user, 'award.done', f"Awarded RFQ-{requirement.pk} to {supplier_name}",
                     reverse('requirement', kwargs={'pk': requirement.pk}))
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
            team.log(request.user, 'quote.rejected', f"Rejected {supplier_name}'s quote on RFQ-{requirement.pk}",
                     reverse('requirement', kwargs={'pk': requirement.pk}))
            emails.notify_quote_not_selected(quote)
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
        if not team.is_teammate(request.user, requirement.user_id):
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
        supplier = team.supplier_profile(request.user)
        if supplier is None or quote.supplier_id != supplier.pk:
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
        supplier = team.supplier_profile(request.user)
        if selected is None or supplier is None or selected.supplier_id != supplier.pk:
            raise Http404

        order = requirement.orders.order_by('-created_at', '-pk').first()
        if order is None:
            # RFQs awarded before orders were created at award time.
            order = services.create_award_order(requirement, selected)

        if status == 'Production' and requirement.status == 'Approved':
            requirement.status = 'Production'
            requirement.save()
            if order is not None and order.status == 'quote_selected':
                order.start_production(note="Supplier started production")
                order.save()
                emails.notify_order_status(order, order.status)
            messages.success(request, "Production started.")
        elif status == 'Completed' and requirement.status == 'Production':
            if order is not None and order.status == 'quote_selected':
                order.start_production(note="Supplier started production")
                order.save()
                emails.notify_order_status(order, order.status)
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
            supplier = team.supplier_profile(user)
            orders = Order.objects.filter(supplier=supplier)
            template_name = "order/order_list_manufacturer.html"
        else:
            customer = team.buyer_profile(user)
            orders = Order.objects.filter(customer=customer)
            template_name = "order/order_list.html"
        listed = orders.select_related('requirement', 'supplier', 'customer').order_by('-created_at')
        can_filter = access.can(user, 'orders.filter')
        status, search = request.GET.get('status', ''), request.GET.get('q', '').strip()
        if can_filter and status in dict(Order.STATUS_CHOICES):
            listed = listed.filter(status=status)
        if can_filter and search:
            listed = listed.filter(Q(requirement__title__icontains=search) | Q(billno__icontains=search.removeprefix('ORD-')))
        if request.GET.get('export') == 'csv':
            refused = _export_refused(request)
            if refused:
                return refused
            return export.csv_response('orders', ['Order', 'RFQ', 'Title', 'Supplier', 'Buyer', 'Status', 'Stage', 'Ship by', 'Created'], (
                [f"ORD-{o.billno}", f"RFQ-{o.requirement_id}", o.requirement.title, o.supplier.companyname or o.supplier,
                 o.customer.Name, o.get_status_display(), o.get_production_stage_display(), o.ship_by_date or '',
                 o.created_at.strftime('%Y-%m-%d')]
                for o in listed
            ))
        common = {
            'can_filter_orders': can_filter, 'can_export': access.can(user, 'export'),
            'status_filter': status, 'q': search, 'status_choices': Order.STATUS_CHOICES,
        }
        if user.role == 'manufacturer':
            context = {'bills': listed, **common}
        else:
            month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            context = {
                **common,
                'bills': listed,
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
        party = _order_role(order, request.user)
        if not (request.user.is_staff or party):
            raise Http404
        requirement = order.requirement
        items = RequirementPart.objects.filter(requirement=requirement.id)
        quote = order.quote
        supplier = order.supplier
        customer = order.customer
        breakdown = quote.get_breakdown()
        next_status = _next_order_status(order)
        is_supplier = party == 'supplier'
        can_advance = (
            request.user.is_authenticated and next_status is not None
            and _may_perform_transition(order, request.user, next_status)
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
            'review_form': SupplierReviewForm(),
            'order_documents': order.documents.all(),
            'is_buyer': party == 'buyer',
            'payment_approval': order.approval_requests.filter(kind=ApprovalRequest.PAYMENT, status=ApprovalRequest.PENDING).first(),
            'payment_for_admins': next_status == 'paid' and party == 'buyer' and not team.allows(request.user, 'order.confirm_payment'),
        }
        return render(request, self.template_name, context)


class OrderProductionAdvanceView(LoginRequiredMixin, View):
    def post(self, request, billno):
        order = get_object_or_404(Order, billno=billno)
        if _order_role(order, request.user) != 'supplier':
            messages.error(request, "Only the manufacturer on this order can update production.")
            return redirect(reverse('order-detail', kwargs={'billno': order.billno}))
        advanced = order.advance_production_stage(note=request.POST.get('note', ''))
        if advanced:
            messages.success(request, f"Marked '{order.get_production_stage_display()}' complete.")
        if advanced and order.production_stage == 'dispatched':
            invoice = documents.issue_invoice(order)
            emails.notify_order_dispatched(order, invoice)
            messages.success(request, f"Tax invoice {invoice.number} issued to the buyer.")
            if getattr(request, 'htmx', False):
                # The invoice appears in the Documents card, outside the swapped tracker.
                response = HttpResponse(status=204)
                response['HX-Refresh'] = 'true'
                return response
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
        if _order_role(order, request.user) != 'supplier':
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
        if _order_role(order, request.user) != 'supplier':
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
        if _order_role(order, request.user) != 'supplier':
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
        if _order_role(order, request.user) is None:
            raise Http404
        if status == 'paid' and _may_perform_transition(order, request.user, status):
            # Confirming payment is the other step that moves money.
            reauth = session_security.require_recent_auth(request, reverse('order-detail', kwargs={'billno': order.billno}))
            if reauth is not None:
                return reauth
        transition_name = ORDER_TRANSITIONS.get(status)
        if not (transition_name and hasattr(order, transition_name)):
            logger.warning("Unknown order status '%s' requested for order #%s", status, billno)
            messages.error(request, "Unknown order status.")
        elif not _may_perform_transition(order, request.user, status):
            logger.warning(
                "Rejected transition '%s' on order #%s (current status: %s): %s is not the party who may make it",
                status, order.billno, order.status, request.user,
            )
            if status == 'paid' and _order_role(order, request.user) == 'buyer':
                messages.error(request, "Only your company's owner or an admin can confirm a payment.")
            elif ORDER_TRANSITION_ROLE.get(status, _order_role(order, request.user)) == _order_role(order, request.user) and status != 'cancelled':
                messages.error(request, f"Your role ({team.role_label(request.user)}) can't make that change.")
            elif status == 'cancelled':
                messages.error(request, "This order can only be cancelled before production starts.")
            elif ORDER_TRANSITION_ROLE.get(status) == 'supplier':
                messages.error(request, "Only the manufacturer on this order can make that change.")
            else:
                messages.error(request, "Only the buyer on this order can make that change.")
        else:
            transition_method = getattr(order, transition_name)
            try:
                transition_method(note=request.POST.get('note', ''))
                order.save()
                services.sync_after_order_transition(order)
                logger.info("Order #%s -> %s by %s", order.billno, order.status, request.user)
                if order.status == 'paid':
                    approvals.cancel_open("Payment was confirmed directly.", order=order, kind=ApprovalRequest.PAYMENT)
                team.log(request.user, f"order.{order.status}", f"Moved ORD-{order.billno} to {order.get_status_display()}",
                         reverse('order-detail', kwargs={'billno': order.billno}))
                emails.notify_order_status(order, order.status)
                messages.success(request, f"Order moved to {order.get_status_display()}.")
            except TransitionNotAllowed:
                logger.warning(
                    "Rejected transition '%s' on order #%s (current status: %s) by %s",
                    status, order.billno, order.status, request.user,
                )
                messages.error(request, "That status change isn't allowed from the order's current state.")
        if getattr(request, 'htmx', False):
            if order.status in ('in_production', 'completed', 'cancelled'):
                # These change the tracker, QC checklist and rating card
                # outside the swapped status block; reload to show them.
                response = HttpResponse(status=204)
                response['HX-Refresh'] = 'true'
                return response
            next_status = _next_order_status(order)
            can_advance = (
                request.user.is_authenticated and next_status is not None
                and _may_perform_transition(order, request.user, next_status)
            )
            return render(request, 'order/_order_status.html', {
                'bill': order,
                'next_status': next_status,
                'can_advance': can_advance,
            })
        return redirect(reverse('order-detail', kwargs={'billno': order.billno}))


class OrderReviewCreateView(LoginRequiredMixin, View):
    """The buyer rates the manufacturer once the order is completed. One
    review per order; it can't be edited afterwards."""
    def post(self, request, billno):
        order = get_object_or_404(Order.objects.select_related('customer', 'supplier'), billno=billno)
        if _order_role(order, request.user) != 'buyer':
            raise Http404
        detail_url = reverse('order-detail', kwargs={'billno': order.billno}) + '#review'
        if order.status != 'completed':
            messages.error(request, "You can rate the manufacturer once the order is completed.")
            return redirect(detail_url)
        if SupplierReview.objects.filter(order=order).exists():
            messages.error(request, "You've already rated this order.")
            return redirect(detail_url)
        form = SupplierReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.order = order
            review.save()
            logger.info("Order #%s rated %s/5 by %s", order.billno, review.rating, request.user)
            messages.success(request, "Thanks — your rating has been saved.")
        else:
            messages.error(request, "Pick a rating from 1 to 5 stars.")
        return redirect(detail_url)


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
        return services.visible_requirements_for(self.request.user)

    STATUS_FILTERS = {
        'open': Q(status__isnull=True),
        'awarded': Q(status='Approved'),
        'production': Q(status='Production'),
        'completed': Q(status='Completed'),
    }

    def _filters(self):
        get = self.request.GET
        return {
            'status': get.get('status', ''),
            'process': get.get('process', ''),
            'material': get.get('material', ''),
            'date_from': get.get('date_from', ''),
            'date_to': get.get('date_to', ''),
        }

    def get_queryset(self):
        query = (self.request.GET.get('search') or '').strip()
        filters = self._filters()
        if not query and not any(filters.values()):
            return Requirement.objects.none()
        queryset = self._visible_requirements()
        if filters['status'] in self.STATUS_FILTERS:
            queryset = queryset.filter(self.STATUS_FILTERS[filters['status']])
        if filters['process']:
            queryset = queryset.filter(pk__in=RequirementPart.objects.filter(technology=filters['process']).values('requirement'))
        if filters['material']:
            queryset = queryset.filter(pk__in=RequirementPart.objects.filter(Material=filters['material']).values('requirement'))
        for key, lookup in (('date_from', 'created_at__date__gte'), ('date_to', 'created_at__date__lte')):
            day = parse_date(filters[key]) if filters[key] else None
            if day:
                queryset = queryset.filter(**{lookup: day})
        if not query:
            return queryset.order_by('-created_at')
        number = RFQ_NUMBER_PATTERN.match(query)
        if number:
            by_number = queryset.filter(pk=int(number.group(1))).order_by('pk')
            if by_number.exists():
                return by_number
        return search.search_requirements(queryset, query).order_by('-rank', '-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['filters'] = self._filters()
        context['has_filters'] = any(context['filters'].values())
        context['process_choices'] = RequirementPart.TECHNOLOGY_TYPES
        context['material_choices'] = RequirementPart.MATERIAL_TYPES
        # Everything except the page number, for pagination links.
        params = self.request.GET.copy()
        params.pop('page', None)
        context['querystring'] = params.urlencode()
        return context


class DocumentListView(LoginRequiredMixin, View):
    """Every document on the company's account — POs, invoices, quote files,
    certificates, attachments — on plans with full invoice management. On
    Basic, each order's PO and invoice download from the order page."""
    def get(self, request):
        decision = access.check(request.user, 'invoice.list')
        if not decision:
            return _upgrade_page(request, decision, "Documents")
        if request.GET.get('export') == 'csv':
            refused = _export_refused(request)
            if refused:
                return refused
            if request.user.role == 'manufacturer':
                found = OrderDocument.objects.filter(order__supplier=team.supplier_profile(request.user))
            else:
                found = OrderDocument.objects.filter(order__customer=team.buyer_profile(request.user))
            return export.csv_response('invoices', ['Number', 'Type', 'Order', 'Issued', 'Currency', 'Total'], (
                [doc.number, doc.get_kind_display(), f"ORD-{doc.order_id}", doc.issued_at.strftime('%Y-%m-%d'), doc.currency, doc.total]
                for doc in found.order_by('-issued_at')
            ))
        if request.user.role == 'manufacturer':
            # A manufacturer's own uploads (quote files) plus whatever the
            # buyer shared back through production updates on their orders.
            supplier = team.supplier_profile(request.user)
            if supplier is None:
                raise Http404
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
        docs += services.order_document_entries(request.user)
        docs.sort(key=lambda doc: doc['date'], reverse=True)
        return render(request, 'documents/document_list.html', {'docs': docs, 'can_export': access.can(request.user, 'export')})


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
            supplier = team.supplier_profile(request.user)
            can_see = supplier is not None and (
                # Asking about an open RFQ receives it, like opening it does.
                (services.open_requirements_for(supplier).filter(pk=requirement.pk).exists() and rfq_inbox.receive(supplier, requirement))
                or Quote.objects.filter(requirement=requirement, supplier=supplier).exists()
                or MessageThread.objects.filter(requirement=requirement, supplier=supplier).exists()
            )
            if not can_see:
                raise Http404
        else:
            if not team.is_teammate(request.user, requirement.user_id):
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
    # A draft is the supplier's unsent work: only its author sees it. It
    # used to be listed (price included) to the buyer, and counted in the
    # Best price / Fastest comparison, before it was ever submitted.
    is_manufacturer = request.user.role == 'manufacturer'
    own_supplier = team.supplier_profile(request.user) if is_manufacturer else None
    quotes = services.annotate_quote_badges(
        Quote.objects.filter(requirement=requirement, is_deleted=False)
        .filter(Q(is_draft=False) | Q(supplier=own_supplier))
        .select_related('supplier', 'supplier__company')
    )
    my_quote = None
    my_declined = False
    if is_manufacturer:
        supplier = own_supplier
        my_quote = next((q for q in quotes if q.supplier_id == getattr(supplier, 'pk', None)), None)
        my_declined = supplier is not None and RFQDecline.objects.filter(requirement=requirement, supplier=supplier).exists()
    if is_manufacturer:
        # A manufacturer already sees their own price and terms (it's
        # their own data); every other card would show a rival's name,
        # city, rating and note, plus the two comparison badges — which by
        # existing at all tell a supplier the exact thing prices are
        # hidden to prevent: who else is bidding, and whether they're
        # winning on price or lead time. So they see only their own card,
        # with those two badges stripped off it too, plus how many others
        # exist.
        visible_quotes = [my_quote] if my_quote else []
        for quote in visible_quotes:
            quote.badge_best_price = False
            quote.badge_fastest = False
        other_quote_count = len(quotes) - len(visible_quotes)
    else:
        visible_quotes = quotes
        other_quote_count = 0
    return {
        'demand': requirement,
        'quotes': quotes,
        'visible_quotes': visible_quotes,
        'other_quote_count': other_quote_count,
        'is_manufacturer': is_manufacturer,
        'is_buyer': team.is_teammate(request.user, requirement.user_id),
        'my_quote': my_quote,
        'my_quote_pending_revision': approvals.pending_revision(my_quote) if my_quote else None,
        'pending_award': requirement.approval_requests.filter(kind=ApprovalRequest.AWARD, status=ApprovalRequest.PENDING)
        .select_related('quote__supplier', 'requested_by').first(),
        'my_declined': my_declined,
        'selected_quote': next((q for q in quotes if q.is_selected), None),
        'quotes_sig': services.quotes_signature(requirement),
        **_quote_plan_features(request, requirement, quotes, my_quote),
    }


def _quote_plan_features(request, requirement, quotes, my_quote):
    """The parts of the RFQ page that depend on the company's plan."""
    user = request.user
    if user.role == 'manufacturer':
        history = access.can(user, 'quotes.history') and my_quote is not None
        return {'my_quote_revisions': list(my_quote.revisions.select_related('changed_by')) if history else None}
    if not team.is_teammate(user, requirement.user_id):
        return {}
    accept_mode, accept_blocked = _acceptance(user)
    features = {'accept_mode': accept_mode, 'accept_blocked': accept_blocked}
    compare = access.check(user, 'quotes.compare')
    submitted = [q for q in quotes if not q.is_draft]
    if compare and len(submitted) > 1:
        rows = []
        for quote in submitted:
            breakdown = quote.get_breakdown()
            rows.append({
                'quote': quote, 'unit_price': breakdown['unit_price'], 'subtotal': breakdown['subtotal'],
                'tooling': breakdown['tooling_cost'], 'gst': breakdown['gst'],
                'landed': breakdown['total'] + (breakdown['tooling_cost'] or 0),
                'lead_time': f"{quote.lead_time_value} {quote.get_lead_time_unit_display().lower()}" if quote.lead_time_value else '',
            })
        cheapest = min(row['landed'] for row in rows)
        for row in rows:
            row['is_cheapest'] = row['landed'] == cheapest
        features['comparison_rows'] = sorted(rows, key=lambda row: row['landed'])
    elif not compare and len(submitted) > 1:
        features['compare_upgrade'] = compare.upgrade_label
    if requirement.status is None and access.can(user, 'suppliers.suggest'):
        features['suggested_suppliers'] = services.suggested_suppliers(requirement, exclude=[q.supplier_id for q in quotes])
    return features


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
    """New or changed quotes on an open RFQ. Same visibility scope as the
    RFQ page itself (services.visible_requirements_for) — otherwise the
    poll would happily hand quote data for an RFQ the page itself
    refuses to open."""
    def get(self, request, pk):
        if not request.user.is_authenticated:
            return _poll_response(STOP_POLLING)
        if request.user.is_staff:
            requirement = get_object_or_404(Requirement, pk=pk)
        else:
            requirement = get_object_or_404(services.visible_requirements_for(request.user), pk=pk)
        if request.GET.get('sig') == services.quotes_signature(requirement):
            return _poll_response(NO_CHANGE)
        return render(request, 'requirement/_quotes_section.html', quotes_section_context(request, requirement))


class ReportsView(LoginRequiredMixin, View):
    """Buyers get their spend report, manufacturers their win-rate report.
    ?period= picks the window; ?format=csv downloads the underlying rows."""
    def get(self, request):
        period = request.GET.get('period', reports.DEFAULT_PERIOD)
        extra = {}
        if request.user.role == 'manufacturer':
            supplier = team.supplier_profile(request.user)
            if supplier is None:
                messages.info(request, "Your manufacturer profile isn't set up yet.")
                return redirect(reverse('home'))
            # Sales analytics (win rate, response) for owner/admin/sales on
            # Starter+; performance insights for owner/admin/operations/
            # viewers on Business.
            sales = access.check(request.user, 'analytics.sales')
            performance = access.check(request.user, 'performance.view')
            if not (sales or performance):
                return _upgrade_page(request, sales if team.allows(request.user, 'analytics.sales') else performance, "Reports")
            report = reports.supplier_win_rate(supplier, period) if sales else {'period': period, 'periods': reports.PERIODS}
            extra = {
                'show_sales': bool(sales),
                'show_price_comparison': access.can(request.user, 'quotes.compare'),
                'performance': reports.supplier_performance(supplier, period) if performance else None,
                'performance_upgrade': '' if performance or not team.allows(request.user, 'analytics.operations') else performance.upgrade_label,
            }
            template, filename = 'reports/supplier_win_rate.html', 'quotes'
        else:
            decision = access.check(request.user, 'analytics.view')
            if not decision:
                return _upgrade_page(request, decision, "Reports")
            report = reports.buyer_spend(request.user, period)
            template, filename = 'reports/buyer_spend.html', 'spend'
        if request.GET.get('format') == 'csv' and extra.get('show_sales', True):
            refused = _export_refused(request)
            if refused:
                return refused
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="{filename}-{report["period"]}.csv"'
            writer = csv.writer(response)
            writer.writerow(report['csv_header'])
            writer.writerows(report['csv_rows'])
            return response
        return render(request, template, {'report': report, 'can_export': access.can(request.user, 'export'), **extra})


class OrderDocumentDownloadView(LoginRequiredMixin, View):
    """A PO or tax invoice PDF, for the order's buyer, its manufacturer or staff."""
    def get(self, request, pk):
        document = get_object_or_404(OrderDocument.objects.select_related('order__supplier', 'order__customer'), pk=pk)
        if not (request.user.is_staff or _order_role(document.order, request.user)):
            raise Http404
        documents.ensure_pdf(document)
        return FileResponse(document.pdf.open('rb'), content_type='application/pdf', filename=f"{document.number}.pdf")


class ApprovalListView(LoginRequiredMixin, View):
    """The company's approval queue. The owner and admins see every pending
    request and decide them; anyone else sees the requests they made."""
    def get(self, request):
        profile = team.company(request.user)
        if profile is None:
            messages.info(request, "Your company profile isn't set up yet.")
            return redirect(reverse('home'))
        can_approve = team.can_manage_company(request.user) and team.company(request.user) is not None
        rows = ApprovalRequest.objects.filter(**team.company_filter(profile)).select_related(
            'requirement', 'quote__requirement', 'quote__supplier', 'order__requirement', 'requested_by', 'decided_by',
        )
        if not can_approve:
            rows = rows.filter(requested_by=request.user)
        pending = list(rows.filter(status=ApprovalRequest.PENDING))
        history = list(rows.exclude(status=ApprovalRequest.PENDING).order_by('-decided_at')[:50])
        for req in pending + history:
            req.summary = approvals.describe(req)
            req.target = approvals.target_url(req)
        return render(request, 'approvals/approval_list.html', {
            'pending': pending, 'history': history, 'can_approve': can_approve,
        })


class ApprovalDecisionView(LoginRequiredMixin, View):
    """The owner or an admin approves or rejects a request. Awards and
    payment confirmations move money, so they need a recent password, the
    same as doing them directly."""
    http_method_names = ['post']

    def post(self, request, pk):
        req = get_object_or_404(
            ApprovalRequest.objects.select_related('requirement', 'quote__requirement', 'quote__supplier', 'order__requirement', 'requested_by'),
            pk=pk,
        )
        if not approvals.can_decide(request.user, req):
            raise Http404
        decision = request.POST.get('decision')
        note = request.POST.get('note', '').strip()[:1000]
        summary = approvals.describe(req)
        if decision == 'approve':
            if req.kind in (ApprovalRequest.AWARD, ApprovalRequest.PAYMENT):
                reauth = session_security.require_recent_auth(request, reverse('approvals'))
                if reauth is not None:
                    return reauth
            try:
                approvals.approve(req, request.user, note)
                messages.success(request, f"Approved: {summary}")
            except approvals.ApprovalError as exc:
                messages.error(request, f"This can no longer be approved — {exc}. The request was cancelled.")
        elif decision == 'reject':
            if not note:
                messages.error(request, "Add a short reason so the requester knows what to change.")
                return redirect(reverse('approvals'))
            try:
                approvals.reject(req, request.user, note)
                messages.success(request, f"Rejected: {summary}")
            except approvals.ApprovalError as exc:
                messages.error(request, str(exc))
        return redirect(reverse('approvals'))


class ContactsView(LoginRequiredMixin, View):
    """A supplier's customer contacts: the buyers on its orders and
    conversations. Basic plans list buyer company names; plans with full
    contacts add the contact person, email, phone and conversation links.
    Viewers always get the names only ('buyer_info.view')."""
    def get(self, request):
        supplier = team.supplier_profile(request.user)
        if supplier is None or not team.allows(request.user, 'contacts.view'):
            raise Http404
        details = access.check(request.user, 'contacts.details')
        buyers = {}
        for order in Order.objects.filter(supplier=supplier).select_related('customer__user').order_by('-created_at'):
            entry = buyers.setdefault(order.customer_id, {'buyer': order.customer, 'orders': 0, 'threads': [], 'last': order.created_at})
            entry['orders'] += 1
        for thread in MessageThread.objects.filter(supplier=supplier).select_related('requirement__user').order_by('-created_at'):
            customer = team.buyer_profile(thread.requirement.user)
            if customer is None:
                continue
            entry = buyers.setdefault(customer.pk, {'buyer': customer, 'orders': 0, 'threads': [], 'last': thread.created_at})
            entry['threads'].append(thread)
        if request.GET.get('export') == 'csv':
            refused = _export_refused(request)
            if refused:
                return refused
            return export.csv_response('contacts', ['Buyer', 'Contact email', 'Phone', 'City', 'Orders'], (
                [e['buyer'].Name, e['buyer'].email if details else '', e['buyer'].phone if details else '', e['buyer'].city, e['orders']]
                for e in buyers.values()
            ))
        return render(request, 'contacts/contact_list.html', {
            'contacts': sorted(buyers.values(), key=lambda e: e['last'], reverse=True),
            'show_details': bool(details),
            'details_upgrade': '' if details or not team.allows(request.user, 'buyer_info.view') else details.upgrade_label,
            'can_export': access.can(request.user, 'export'),
        })


class RFQAlertPreferenceView(LoginRequiredMixin, View):
    """Which RFQs a supplier gets instant alerts about (Business plans:
    advanced RFQ opportunity alerts)."""
    template_name = 'requirement/alert_preferences.html'

    def _context(self, request, supplier, preference):
        return {
            'preference': preference,
            'process_choices': RequirementPart.TECHNOLOGY_TYPES,
            'material_choices': RequirementPart.MATERIAL_TYPES,
        }

    def get(self, request):
        supplier = team.supplier_profile(request.user)
        decision = access.check(request.user, 'rfq.alert_preferences')
        if supplier is None:
            raise Http404
        if not decision:
            return _upgrade_page(request, decision, "RFQ alert preferences")
        preference = RFQAlertPreference.objects.filter(supplier=supplier).first() or RFQAlertPreference(supplier=supplier)
        return render(request, self.template_name, self._context(request, supplier, preference))

    def post(self, request):
        supplier = team.supplier_profile(request.user)
        decision = access.check(request.user, 'rfq.alert_preferences')
        if supplier is None:
            raise Http404
        if not decision:
            messages.error(request, decision.reason)
            return redirect(reverse('rfq-alert-preferences'))
        min_quantity = request.POST.get('min_quantity', '').strip()
        RFQAlertPreference.objects.update_or_create(supplier=supplier, defaults={
            'processes': [p[:100] for p in request.POST.getlist('processes')][:50],
            'materials': [m[:100] for m in request.POST.getlist('materials')][:50],
            'min_quantity': int(min_quantity) if min_quantity.isdigit() else None,
        })
        team.log(request.user, 'rfq.alerts_updated', "Updated RFQ alert preferences", reverse('rfq-alert-preferences'))
        messages.success(request, "RFQ alert preferences saved.")
        return redirect(reverse('rfq-alert-preferences'))
