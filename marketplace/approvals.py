"""Internal approvals: what a team user (accounts.team.MEMBER) does that
must wait for a supervisor's or the manager's sign-off.

- RFQ: a user's new RFQ is saved with approval_status 'pending'. Suppliers
  can't see it (services.open_requirements_for) and aren't emailed until
  it's approved.
- QUOTE: a user's first submission stays a draft (buyers never see drafts)
  until approved. A user's revision of a quote the buyer already has is
  staged in the request's payload instead, so the buyer keeps seeing the
  current quote until the revision is approved.
- AWARD: a user's "award this quote" (directly, or by accepting a
  supplier's pricing for a change request) is held until approved.
- PAYMENT: a user's "confirm payment" on an order is held until approved.

approve() re-checks that the action still makes sense (the RFQ might have
been awarded meanwhile, the quote withdrawn...) and cancels the request
with a reason if not.
"""
import datetime
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts import team
from core.emails import send_template_email
from . import emails, services
from .models import (
    APPROVAL_APPROVED, APPROVAL_REJECTED,
    AmendmentResponse, ApprovalRequest, Quote, RequirementAmendment,
)

logger = logging.getLogger(__name__)

REVISION_FIELDS = ['quote_price', 'tooling_cost', 'lead_time_value', 'lead_time_unit', 'payment_terms', 'valid_until', 'note']


class ApprovalError(Exception):
    """The request can no longer be carried out; the message says why."""


def describe(req):
    if req.kind == ApprovalRequest.RFQ:
        return f"RFQ-{req.requirement_id}: {req.requirement.title}"
    if req.kind == ApprovalRequest.QUOTE:
        quote = req.quote
        prefix = "Revised quote" if req.is_revision else "Quote"
        return f"{prefix} on RFQ-{quote.requirement_id}: {quote.requirement.title}"
    if req.kind == ApprovalRequest.AWARD:
        quote = req.quote
        return f"Award {quote.supplier.companyname or quote.supplier} on RFQ-{quote.requirement_id}: {quote.requirement.title}"
    return f"Payment for ORD-{req.order_id}: {req.order.requirement.title}"


def target_url(req):
    if req.kind == ApprovalRequest.PAYMENT:
        return reverse('order-detail', kwargs={'billno': req.order_id})
    requirement_id = req.requirement_id or req.quote.requirement_id
    return reverse('requirement', kwargs={'pk': requirement_id})


def pending_for(profile):
    if profile is None:
        return ApprovalRequest.objects.none()
    return ApprovalRequest.objects.filter(**team.company_filter(profile), status=ApprovalRequest.PENDING)


def can_decide(user, req):
    return team.can_approve(user) and team.belongs_to(req, team.company(user))


def _absolute(path):
    return f"{settings.SITE_URL}{path}"


def _name(user):
    return (user.get_full_name() or user.email) if user else 'Someone'


def submit(user, kind, *, requirement=None, quote=None, order=None, payload=None, note=''):
    """Files (or refreshes) the pending request for this action and tells
    the company's approvers."""
    profile = team.company(user)
    req = ApprovalRequest.objects.filter(
        kind=kind, status=ApprovalRequest.PENDING, requirement=requirement, quote=quote, order=order,
    ).first()
    if req is None:
        req = ApprovalRequest.objects.create(
            kind=kind, **team.company_filter(profile), requirement=requirement, quote=quote, order=order,
            payload=payload or {}, requested_by=user, note=note[:1000],
        )
    else:
        if payload is not None:
            _discard_staged_file(req)
            req.payload = payload
        req.requested_by = user
        req.note = note[:1000]
        req.save(update_fields=['payload', 'requested_by', 'note'])
    summary = describe(req)
    team.log(user, f"{kind}.requested", f"Asked for approval — {req.get_kind_display().lower()}: {summary}", target_url(req), profile)
    for approver in team.approvers(profile):
        if approver.pk != user.pk:
            send_template_email(
                approver.email, 'emails/approval_requested_subject.txt', 'emails/approval_requested.txt',
                {
                    'approver_name': approver.first_name or approver.email, 'requester_name': _name(user),
                    'kind_label': req.get_kind_display(), 'subject_line': summary, 'note': req.note,
                    'url': _absolute(reverse('approvals')),
                },
                html_template='emails/approval_requested.html',
            )
    services.invalidate_team_notification_cache(profile)
    return req


def approve(req, user, note=''):
    """Carries out the request. Raises ApprovalError (after cancelling the
    request) when it can no longer be done."""
    if req.status != ApprovalRequest.PENDING:
        raise ApprovalError("This request has already been decided.")
    try:
        with transaction.atomic():
            _APPROVE[req.kind](req, user)
            _decide(req, user, ApprovalRequest.APPROVED, note)
    except ApprovalError as exc:
        _decide(req, user, ApprovalRequest.CANCELLED, str(exc))
        team.log(user, f"{req.kind}.cancelled", f"Approval request cancelled ({exc}): {describe(req)}", target_url(req))
        raise
    team.log(user, f"{req.kind}.approved", f"Approved {req.get_kind_display().lower()}: {describe(req)}", target_url(req))
    _notify_requester(req, user)


def reject(req, user, note=''):
    if req.status != ApprovalRequest.PENDING:
        raise ApprovalError("This request has already been decided.")
    with transaction.atomic():
        if req.kind == ApprovalRequest.RFQ:
            req.requirement.approval_status = APPROVAL_REJECTED
            req.requirement.save(update_fields=['approval_status', 'updated_at'])
        elif req.kind == ApprovalRequest.QUOTE:
            if req.is_revision:
                _discard_staged_file(req)
            else:
                req.quote.approval_status = APPROVAL_REJECTED
                req.quote.save(update_fields=['approval_status', 'updated_at'])
        _decide(req, user, ApprovalRequest.REJECTED, note)
    team.log(user, f"{req.kind}.rejected", f"Rejected {req.get_kind_display().lower()}: {describe(req)}", target_url(req))
    _notify_requester(req, user)


def cancel_open(reason, **targets):
    """Cancels pending requests for these targets, e.g. when the RFQ or
    quote is deleted, or a supervisor/manager acts on it directly."""
    for req in ApprovalRequest.objects.filter(status=ApprovalRequest.PENDING, **targets):
        _discard_staged_file(req)
        _decide(req, None, ApprovalRequest.CANCELLED, reason)


def pending_revision(quote):
    return quote.approval_requests.filter(kind=ApprovalRequest.QUOTE, status=ApprovalRequest.PENDING, payload__has_key='revision').first()


def revision_payload(form, quote):
    """The submitted QuoteForm's values, to apply on approval. A newly
    uploaded file is stored now (under a fresh name) and only linked to the
    quote on approval."""
    data = {}
    for field in REVISION_FIELDS:
        value = form.cleaned_data.get(field)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, datetime.date):
            value = value.isoformat()
        data[field] = value
    payload = {'revision': data}
    if 'quote_file' in form.changed_data:
        upload = form.cleaned_data.get('quote_file')
        if upload is False:
            payload['quote_file'] = ''
        elif upload:
            field = Quote._meta.get_field('quote_file')
            payload['quote_file'] = field.storage.save(field.generate_filename(quote, upload.name), upload)
    return payload


def _discard_staged_file(req):
    name = req.payload.get('quote_file') if req.payload else None
    if name:
        Quote._meta.get_field('quote_file').storage.delete(name)


def _decide(req, user, status, note):
    req.status = status
    req.decided_by = user
    req.decided_at = timezone.now()
    req.decision_note = (note or '')[:1000]
    req.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    profile = req.buyer or req.supplier
    services.invalidate_team_notification_cache(profile)


def _notify_requester(req, decider):
    requester = req.requested_by
    if requester is None or requester.pk == decider.pk:
        return
    send_template_email(
        requester.email, 'emails/approval_decided_subject.txt', 'emails/approval_decided.txt',
        {
            'requester_name': requester.first_name or requester.email, 'decider_name': _name(decider),
            'approved': req.status == ApprovalRequest.APPROVED, 'kind_label': req.get_kind_display(),
            'subject_line': describe(req), 'decision_note': req.decision_note, 'url': _absolute(target_url(req)),
        },
        html_template='emails/approval_decided.html',
    )


def _approve_rfq(req, user):
    requirement = req.requirement
    if requirement.is_deleted:
        raise ApprovalError("the RFQ was deleted")
    requirement.approval_status = APPROVAL_APPROVED
    requirement.save(update_fields=['approval_status', 'updated_at'])
    transaction.on_commit(lambda: emails.notify_new_rfq(requirement))


def _approve_quote(req, user):
    quote = req.quote
    if quote.is_deleted or not quote.is_undecided:
        raise ApprovalError("the quote was withdrawn or already decided by the buyer")
    if req.is_revision:
        for field, value in req.payload['revision'].items():
            if value is not None and field in ('quote_price', 'tooling_cost'):
                value = Decimal(value)
            elif value and field == 'valid_until':
                value = datetime.date.fromisoformat(value)
            setattr(quote, field, value)
        if 'quote_file' in req.payload:
            quote.quote_file.name = req.payload['quote_file'] or None
        if quote.revision_requested_at:
            quote.revised_at = timezone.now()
            quote.revision_requested_at = None
            quote.revision_note = ''
        quote.save()
    else:
        if not quote.requirement.is_open_for_quotes():
            raise ApprovalError("the RFQ is no longer open for quotes")
        quote.is_draft = False
        quote.approval_status = APPROVAL_APPROVED
        quote.save()
    transaction.on_commit(lambda: emails.notify_quote_submitted(quote))


def _approve_award(req, user):
    quote = req.quote
    requirement = quote.requirement
    if requirement.is_deleted or requirement.status or not quote.is_undecided or quote.is_draft:
        raise ApprovalError("the RFQ was already awarded or the quote is no longer open")
    response_id = req.payload.get('amendment_response')
    if response_id:
        response = AmendmentResponse.objects.select_related('amendment').filter(pk=response_id, quote=quote).first()
        if (response is None or response.status != AmendmentResponse.ACCEPTED
                or response.amendment.status != RequirementAmendment.PENDING):
            raise ApprovalError("the supplier's updated pricing can no longer be accepted")
        services.accept_amendment_pricing(response)
    else:
        services.award_quote(requirement, quote)


def _approve_payment(req, user):
    order = req.order
    if order.status != 'payment_pending':
        raise ApprovalError("the order is no longer waiting for payment")
    order.mark_paid(note=f"Payment confirmed by {_name(user)} (approval of {_name(req.requested_by)}'s request)")
    order.save()
    services.sync_after_order_transition(order)
    transaction.on_commit(lambda: emails.notify_order_status(order, order.status))


_APPROVE = {
    ApprovalRequest.RFQ: _approve_rfq,
    ApprovalRequest.QUOTE: _approve_quote,
    ApprovalRequest.AWARD: _approve_award,
    ApprovalRequest.PAYMENT: _approve_payment,
}


def feed_events(user):
    """Notification-bell entries: pending requests for approvers, decisions
    on the user's own requests."""
    profile = team.company(user)
    if profile is None:
        return []
    events = []
    if team.can_approve(user):
        for req in pending_for(profile).select_related('requirement', 'quote__requirement', 'quote__supplier', 'order__requirement', 'requested_by')[:20]:
            events.append({
                'key': f'approval:{req.pk}', 'kind': 'approval',
                'title': f"Approval needed · {req.get_kind_display()}",
                'detail': f"{_name(req.requested_by)} — {describe(req)}",
                'url': reverse('approvals'), 'timestamp': req.created_at,
            })
    decided = ApprovalRequest.objects.filter(
        requested_by=user, status__in=[ApprovalRequest.APPROVED, ApprovalRequest.REJECTED, ApprovalRequest.CANCELLED],
    ).select_related('requirement', 'quote__requirement', 'quote__supplier', 'order__requirement', 'decided_by')[:20]
    for req in decided:
        verb = {ApprovalRequest.APPROVED: 'Approved', ApprovalRequest.REJECTED: 'Not approved'}.get(req.status, 'Cancelled')
        events.append({
            'key': f'approval-decision:{req.pk}', 'kind': 'approval',
            'title': f"{verb} · {req.get_kind_display()}",
            'detail': describe(req) + (f" — {req.decision_note}" if req.decision_note else ''),
            'url': target_url(req), 'timestamp': req.decided_at,
        })
    return events
