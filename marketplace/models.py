import os
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django_fsm import FSMField, transition

from accounts.models import ManufacturerProfile, ConsumerProfile

GST_RATE = Decimal('0.18')


# Contains requirements (RFQs) submitted by consumers
class Requirement(models.Model):
    REQUIREMENT_STATUS = [
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Production', 'Production'),
        ('Completed', 'Completed'),
    ]
    CURRENCY_CHOICES = [
        ('INR', 'Indian Rupee'),
        ('USD', 'US Dollar'),
        ('EUR', 'Euro'),
        ('JPY', 'Japanese Yen'),
        ('GBP', 'British Pound'),
        ('AUD', 'Australian Dollar'),
        ('CAD', 'Canadian Dollar'),
        ('CHF', 'Swiss Franc'),
        ('CNY', 'Chinese Yuan'),
        ('SEK', 'Swedish Krona'),
        ('NZD', 'New Zealand Dollar'),
        # Add more currencies as needed
    ]
    REQUEST_REASON_CHOICES = [
        ('new_product', 'New product'),
        ('second_source', 'Second source'),
        ('supplier_failure', 'Supplier failure'),
        ('benchmarking', 'Benchmarking'),
        ('other', 'Other'),
    ]
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200, verbose_name='Name')
    rfq_desc = models.CharField(max_length=400, verbose_name='Name')
    nda_required = models.BooleanField(default=False)
    quote_currency = models.CharField(max_length=50, choices=CURRENCY_CHOICES)
    request_reason = models.CharField(max_length=50, choices=REQUEST_REASON_CHOICES)
    parts = models.IntegerField(default=1)
    end_date = models.DateTimeField(blank=True, null=True)
    industry = models.CharField(max_length=200, blank=True, null=True)
    file = models.FileField(upload_to='requirement_files/', blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True, choices=REQUIREMENT_STATUS)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Requirement #{self.id} - {self.user.first_name}"

    def total_parts_quantity(self):
        return sum(part.quantity for part in self.requirement_parts.all())

    def is_open_for_quotes(self):
        """Same rule as services.open_requirements_for: not deleted, not yet
        awarded, and quotes not yet due."""
        if self.is_deleted or self.status or self.quote.filter(is_selected=True).exists():
            return False
        return self.end_date is None or self.end_date >= timezone.now()

    def live_quotes(self):
        """Submitted quotes still in play (not drafts, not deleted, not decided)."""
        return self.quote.filter(is_deleted=False, is_draft=False, is_selected=False, status__isnull=True)

    def is_expired_without_quotes(self):
        """Quotes were due, the RFQ is still unawarded, and nobody quoted."""
        return (
            not self.is_deleted and not self.status and self.end_date is not None
            and self.end_date < timezone.now()
            and not self.quote.filter(is_deleted=False, is_draft=False).exists()
        )

    def pending_amendment(self):
        return self.amendments.filter(status=RequirementAmendment.PENDING).first()

    def is_finished(self):
        """Work on this RFQ is over: it's Completed, or its order was
        completed or cancelled. Its conversations may be closed from then."""
        return self.status == 'Completed' or self.orders.filter(status__in=['completed', 'cancelled']).exists()


class RequirementPart(models.Model):
    TECHNOLOGY_TYPES = [
        ('Anodizing', 'Anodizing'),
        ('Full-range turning', 'Full-range turning'),
        ('Turning', 'Turning'),
        ('Milling', 'Milling'),
    ]
    MATERIAL_TYPES = [
        ('Structural steel', 'Structural steel'),
        ('Stainless steel', 'Stainless steel'),
        ('Aluminium', 'Aluminium'),
        ('Case hardening', 'Case hardening'),
    ]
    id = models.AutoField(primary_key=True)
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='requirement_parts')
    part_name = models.CharField(max_length=100, verbose_name='Name')
    Part_desc = models.CharField(max_length=200, blank=True, null=True)
    technology = models.CharField(max_length=50, choices=TECHNOLOGY_TYPES)
    Material = models.CharField(max_length=50, choices=MATERIAL_TYPES)
    file = models.FileField(upload_to='requirement_files/parts/', blank=True, null=True)
    quantity = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"RequirementPart #{self.id} - {self.requirement.title}"


class Quote(models.Model):
    QUOTE_STATUS = [
        ('Approved', 'Approved'),
        ('Hold', 'Hold'),
        ('Rejected', 'Rejected'),
    ]
    LEAD_TIME_UNIT_CHOICES = [
        ('days', 'Days'),
        ('weeks', 'Weeks'),
    ]
    PAYMENT_TERMS_CHOICES = [
        ('100_advance', '100% advance'),
        ('50_50', '50% advance / 50% on delivery'),
        ('net_15', 'Net 15'),
        ('net_30', 'Net 30'),
        ('net_45', 'Net 45'),
        ('against_lc', 'Against LC'),
    ]
    id = models.AutoField(primary_key=True)
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='quote')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='quotes')
    # Unit price. Already treated as a per-unit price elsewhere (see
    # OrderDetailView's total calc: total_quantity * quote_price) — kept as
    # one field rather than adding a parallel "unit_price" to avoid two
    # sources of truth for the same number.
    quote_price = models.DecimalField(max_digits=10, decimal_places=2)
    tooling_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    lead_time_value = models.PositiveIntegerField(blank=True, null=True)
    lead_time_unit = models.CharField(max_length=10, choices=LEAD_TIME_UNIT_CHOICES, default='days')
    payment_terms = models.CharField(max_length=20, choices=PAYMENT_TERMS_CHOICES, blank=True)
    valid_until = models.DateField(blank=True, null=True)
    quote_file = models.FileField(upload_to='quote_files/', blank=True, null=True)
    is_draft = models.BooleanField(default=False)
    # "Notes to buyer" — widened from a 200-char CharField to a TextField
    # (same column, no data loss) now that the quote form has more to say.
    note = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True, choices=QUOTE_STATUS)
    is_selected = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    # Set when the buyer awards or rejects this quote (including the
    # automatic rejection of the others at award) — drives the supplier's
    # notification.
    decided_at = models.DateTimeField(blank=True, null=True)
    # Buyer asked the supplier to revise this quote. It stays undecided
    # (status stays empty) so it can still be awarded or rejected; the
    # request is cleared when the supplier submits a revision.
    revision_requested_at = models.DateTimeField(blank=True, null=True)
    revision_note = models.TextField(blank=True)
    revised_at = models.DateTimeField(blank=True, null=True)
    # Supplier chose to keep their quote as it was. Not notified to the
    # buyer; the quote simply goes back on the buyer's review pile.
    revision_declined_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Quote #{self.id} - {self.requirement.title}"

    @property
    def is_undecided(self):
        return not self.is_selected and not self.status

    @property
    def awaiting_revision(self):
        return self.revision_requested_at is not None and self.is_undecided

    def pending_amendment_response(self):
        """A buyer change request this supplier still has to answer, if any."""
        return self.amendment_responses.filter(status='pending').select_related('amendment').first()

    @property
    def revision_was_declined(self):
        """The latest revision request was turned down (not superseded by a later revision)."""
        return (
            self.revision_declined_at is not None and self.revision_requested_at is None
            and (self.revised_at is None or self.revised_at < self.revision_declined_at)
        )

    def get_breakdown(self):
        quantity = self.requirement.total_parts_quantity()
        unit_price = self.quote_price or Decimal('0')
        subtotal = (unit_price * quantity) + self.tooling_cost
        gst = (subtotal * GST_RATE).quantize(Decimal('0.01'))
        return {
            'quantity': quantity,
            'unit_price': unit_price,
            'subtotal': subtotal,
            'tooling_cost': self.tooling_cost,
            'gst': gst,
            'total': subtotal + gst,
        }

    def lead_time_as_timedelta(self):
        if not self.lead_time_value:
            return None
        unit = 'weeks' if self.lead_time_unit == 'weeks' else 'days'
        return timedelta(**{unit: self.lead_time_value})


class Order(models.Model):
    STATUS_CHOICES = [
        ('submitted', 'Submitted'),
        ('quoted', 'Quoted'),
        ('quote_selected', 'Quote Selected'),
        ('in_production', 'In Production'),
        ('payment_pending', 'Payment Pending'),
        ('paid', 'Paid'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    # Shop-floor production tracking. Deliberately a plain field, not a
    # second FSMField: these 6 stages are a strictly linear checklist
    # advanced by one "mark complete" button, and keeping this independent
    # of the commercial `status` FSM above avoids entangling two different
    # state machines. Nothing here auto-triggers `status` transitions or
    # vice versa (see `start_production()`, which seeds this at the one
    # point the two intentionally intersect).
    PRODUCTION_STAGE_CHOICES = [
        ('order_confirmed', 'Order confirmed'),
        ('material_received', 'Material received'),
        ('machining', 'Machining'),
        ('finishing_qc', 'Finishing & QC'),
        ('dispatched', 'Dispatched'),
        ('delivered', 'Delivered'),
    ]
    PRODUCTION_STAGES = [key for key, _ in PRODUCTION_STAGE_CHOICES]

    COURIER_CHOICES = [
        ('delhivery', 'Delhivery'),
        ('bluedart', 'Blue Dart'),
        ('dtdc', 'DTDC'),
        ('fedex', 'FedEx'),
        ('other', 'Other'),
    ]

    billno = models.AutoField(primary_key=True)
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='orders')
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='orders')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='orders')
    customer = models.ForeignKey(ConsumerProfile, on_delete=models.CASCADE, related_name='orders')
    status = FSMField(default='submitted', choices=STATUS_CHOICES)
    production_stage = models.CharField(max_length=20, choices=PRODUCTION_STAGE_CHOICES, default='order_confirmed')
    ship_by_date = models.DateField(blank=True, null=True)
    courier = models.CharField(max_length=20, choices=COURIER_CHOICES, blank=True)
    courier_other = models.CharField(max_length=50, blank=True)
    tracking_number = models.CharField(max_length=60, blank=True)
    eway_bill_number = models.CharField(max_length=30, blank=True)
    # The 5 fixed QC checks (QC_CHECKLIST_ITEMS), stored on the order as a
    # list of {"label", "checked", "checked_at", "checked_by"} — they never
    # vary per order, so they don't need a table of their own.
    qc_checklist = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Order no: " + str(self.billno)

    def get_items_list(self):
        return RequirementPart.objects.filter(requirement=self.requirement_id)

    def _log(self, from_status, to_status, note=''):
        OrderEvent.objects.create(order=self, kind=OrderEvent.KIND_STATUS, from_value=from_status, to_value=to_status, note=note or '')

    def status_events(self):
        return self.events.filter(kind=OrderEvent.KIND_STATUS)

    def stage_events(self):
        return self.events.filter(kind=OrderEvent.KIND_STAGE)

    def production_progress_percent(self):
        index = self.PRODUCTION_STAGES.index(self.production_stage)
        return (index + 1) * 100 // len(self.PRODUCTION_STAGES)

    def next_production_stage(self):
        index = self.PRODUCTION_STAGES.index(self.production_stage)
        if index + 1 >= len(self.PRODUCTION_STAGES):
            return None
        return self.PRODUCTION_STAGES[index + 1]

    def advance_production_stage(self, note=''):
        """Plain (non-FSM) forward-only advance of the shop-floor tracker.
        Gate calling this on `status in ('in_production', 'payment_pending',
        'paid')` in the view/template — nothing here enforces that, by
        design, since the two fields are intentionally independent."""
        next_stage = self.next_production_stage()
        if next_stage is None:
            return False
        from_stage = self.production_stage
        self.production_stage = next_stage
        self.save(update_fields=['production_stage', 'updated_at'])
        OrderEvent.objects.create(order=self, kind=OrderEvent.KIND_STAGE, from_value=from_stage, to_value=next_stage, note=note or '')
        return True

    def _seed_qc_checklist(self):
        existing = {item['label'] for item in self.qc_checklist}
        for label in QC_CHECKLIST_ITEMS:
            if label not in existing:
                self.qc_checklist.append({'label': label, 'checked': False, 'checked_at': None, 'checked_by': None, 'checked_by_name': ''})

    def toggle_qc_item(self, index, user):
        """Flips one checklist item and records who did it. Returns False for an unknown index."""
        if not 0 <= index < len(self.qc_checklist):
            return False
        item = self.qc_checklist[index]
        item['checked'] = not item['checked']
        if item['checked']:
            item['checked_at'] = timezone.now().isoformat()
            item['checked_by'] = user.pk
            item['checked_by_name'] = user.get_full_name() or user.email
        else:
            item.update({'checked_at': None, 'checked_by': None, 'checked_by_name': ''})
        self.save(update_fields=['qc_checklist', 'updated_at'])
        return True

    @transition(field=status, source='submitted', target='quoted')
    def mark_quoted(self, note=''):
        self._log('submitted', 'quoted', note)

    @transition(field=status, source='quoted', target='quote_selected')
    def select_quote(self, note=''):
        self._log('quoted', 'quote_selected', note)

    @transition(field=status, source='quote_selected', target='in_production')
    def start_production(self, note=''):
        self._log('quote_selected', 'in_production', note)
        self._seed_qc_checklist()
        lead_time = self.quote.lead_time_as_timedelta()
        if lead_time is not None:
            self.ship_by_date = timezone.now().date() + lead_time

    @transition(field=status, source='in_production', target='payment_pending')
    def request_payment(self, note=''):
        self._log('in_production', 'payment_pending', note)

    @transition(field=status, source='payment_pending', target='paid')
    def mark_paid(self, note=''):
        self._log('payment_pending', 'paid', note)

    @transition(field=status, source='paid', target='completed')
    def complete(self, note=''):
        self._log('paid', 'completed', note)

    @transition(field=status, source=[
        'submitted', 'quoted', 'quote_selected', 'in_production', 'payment_pending',
    ], target='cancelled')
    def cancel(self, note=''):
        self._log(self.status, 'cancelled', note)


class OrderEvent(models.Model):
    """One timeline for everything that happens to an order: a commercial
    status change (kind "status", e.g. quoted -> quote_selected) or a
    shop-floor stage change (kind "stage", e.g. machining -> finishing_qc)."""
    KIND_STATUS = 'status'
    KIND_STAGE = 'stage'
    KIND_CHOICES = [(KIND_STATUS, 'Status change'), (KIND_STAGE, 'Production stage change')]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    from_value = models.CharField(max_length=30, blank=True)
    to_value = models.CharField(max_length=30)
    note = models.CharField(max_length=200, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['changed_at', 'pk']
        indexes = [models.Index(fields=['order', 'kind'])]

    def __str__(self):
        return f"Order #{self.order_id} {self.kind}: {self.from_value or '-'} -> {self.to_value}"

    def to_label(self):
        labels = dict(Order.STATUS_CHOICES if self.kind == self.KIND_STATUS else Order.PRODUCTION_STAGE_CHOICES)
        return labels.get(self.to_value, self.to_value)

    def from_label(self):
        labels = dict(Order.STATUS_CHOICES if self.kind == self.KIND_STATUS else Order.PRODUCTION_STAGE_CHOICES)
        return labels.get(self.from_value, self.from_value)


# Fixed, not manufacturer-configurable — matches the QC checklist shown in
# the manufacturer dashboard mockup. Seeded onto Order.qc_checklist in
# Order.start_production().
QC_CHECKLIST_ITEMS = [
    "First article inspection passed",
    "Dimensional report attached",
    "Material certification verified",
    "Visual / cosmetic inspection passed",
    "Packaging inspected",
]


class ProductionUpdate(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='updates')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    body = models.TextField()
    photo = models.ImageField(upload_to='order_updates/photos/', blank=True, null=True)
    document = models.FileField(upload_to='order_updates/documents/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Update on order #{self.order_id} by {self.author}"


class MessageThread(models.Model):
    """One conversation between a buyer and a supplier, scoped to the RFQ
    they're discussing — matches how every thread in the buyer/supplier
    messaging screens is grouped (always shown with its RFQ or order, never
    as a bare DM). Read state is tracked per side with a single timestamp
    rather than per-message flags, since there are only ever two
    participants."""
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='message_threads')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='message_threads')
    buyer_last_read_at = models.DateTimeField(blank=True, null=True)
    supplier_last_read_at = models.DateTimeField(blank=True, null=True)
    # Set automatically when the order is completed/cancelled, or by either
    # participant once the RFQ is finished (see Requirement.is_finished).
    # A closed thread is read-only: no new messages, no deletions.
    closed_at = models.DateTimeField(blank=True, null=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('requirement', 'supplier')]
        ordering = ['-created_at']

    def __str__(self):
        return f"Thread: requirement #{self.requirement_id} <-> supplier #{self.supplier_id}"

    @property
    def is_closed(self):
        return self.closed_at is not None

    def can_be_closed(self):
        return not self.is_closed and self.requirement.is_finished()

    def close(self, user=None):
        self.closed_at = timezone.now()
        self.closed_by = user
        self.save(update_fields=['closed_at', 'closed_by'])

    def is_participant(self, user):
        return user in (self.requirement.user, self.supplier.user)

    def last_message(self):
        return self.messages.order_by('-created_at').first()

    def unread_count_for(self, user):
        is_buyer = user == self.requirement.user
        last_read = self.buyer_last_read_at if is_buyer else self.supplier_last_read_at
        qs = self.messages.exclude(sender=user).filter(is_deleted=False)
        if last_read:
            qs = qs.filter(created_at__gt=last_read)
        return qs.count()

    def mark_read_for(self, user):
        now = timezone.now()
        is_buyer = user == self.requirement.user
        field = 'buyer_last_read_at' if is_buyer else 'supplier_last_read_at'
        setattr(self, field, now)
        self.save(update_fields=[field])


def message_attachment_path(instance, filename):
    # Random name so storage URLs can't be guessed; downloads go through
    # MessageAttachmentDownloadView, which checks the viewer is a participant.
    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"message_attachments/{uuid.uuid4().hex}{ext}"


class Message(models.Model):
    thread = models.ForeignKey(MessageThread, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    body = models.TextField(blank=True)
    attachment = models.FileField(upload_to=message_attachment_path, blank=True)
    attachment_name = models.CharField(max_length=255, blank=True)
    attachment_content_type = models.CharField(max_length=100, blank=True)
    attachment_size = models.PositiveIntegerField(default=0)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Message #{self.pk} on thread #{self.thread_id}"

    @property
    def has_attachment(self):
        return bool(self.attachment) and not self.is_deleted

    def soft_delete(self):
        """Keeps the row (so the conversation still shows "message deleted"
        in place) but wipes the text and removes the stored file."""
        if self.attachment:
            self.attachment.delete(save=False)
        self.body = ''
        self.attachment_name = ''
        self.attachment_content_type = ''
        self.attachment_size = 0
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()


class NotificationRead(models.Model):
    """Read state for the derived notification feed (services.notification_feed).
    The feed itself isn't stored — each event has a stable key such as
    "quote:12" or "order-status:7", and a row here means this user has read
    that event."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_reads')
    key = models.CharField(max_length=64)
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'key')]

    def __str__(self):
        return f"{self.user} read {self.key}"


class RequirementAmendment(models.Model):
    """A buyer's edit to an RFQ that already has quotes. The RFQ is NOT
    changed when this is created: each quoting supplier accepts (with
    updated pricing) or rejects it, and the changes are applied to the RFQ
    only when the buyer accepts a supplier's updated pricing.

    `changes` holds only what differs, e.g.
    {"requirement": {"quote_currency": {"old": "INR", "new": "USD"}},
     "parts": {"12": {"quantity": {"old": 250, "new": 400}}}}"""
    PENDING = 'pending'
    APPLIED = 'applied'
    WITHDRAWN = 'withdrawn'
    CLOSED = 'closed'
    STATUS_CHOICES = [
        (PENDING, 'Awaiting suppliers'),
        (APPLIED, 'Applied'),
        (WITHDRAWN, 'Withdrawn by buyer'),
        (CLOSED, 'Closed when the RFQ was awarded'),
    ]

    # Editable through a change request, with the labels shown in the UI.
    REQUIREMENT_FIELDS = {
        'title': 'Title', 'rfq_desc': 'Description', 'quote_currency': 'Currency',
        'request_reason': 'Reason for request', 'industry': 'Industry', 'nda_required': 'NDA required',
    }
    PART_FIELDS = {
        'part_name': 'Part name', 'Part_desc': 'Part description', 'technology': 'Process',
        'Material': 'Material', 'quantity': 'Quantity',
    }

    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='amendments')
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    changes = models.JSONField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Amendment #{self.pk} to RFQ-{self.requirement_id} ({self.status})"

    def change_rows(self):
        """Flat, display-ready list: [{"label", "old", "new"}, ...]."""
        rows = []
        for field, diff in self.changes.get('requirement', {}).items():
            rows.append({'label': self.REQUIREMENT_FIELDS.get(field, field), 'old': _display(diff['old']), 'new': _display(diff['new'])})
        parts = {str(p.pk): p for p in self.requirement.requirement_parts.all()}
        for part_pk, fields in self.changes.get('parts', {}).items():
            part = parts.get(part_pk)
            name = part.part_name if part else f"Part #{part_pk}"
            for field, diff in fields.items():
                rows.append({'label': f"{name} \u00b7 {self.PART_FIELDS.get(field, field)}", 'old': _display(diff['old']), 'new': _display(diff['new'])})
        return rows

    def apply(self):
        """Writes the proposed values onto the RFQ and its parts."""
        requirement = self.requirement
        for field, diff in self.changes.get('requirement', {}).items():
            setattr(requirement, field, diff['new'])
        requirement.save()
        for part in requirement.requirement_parts.filter(pk__in=[int(pk) for pk in self.changes.get('parts', {})]):
            for field, diff in self.changes['parts'][str(part.pk)].items():
                setattr(part, field, diff['new'])
            part.save()
        self.status = self.APPLIED
        self.applied_at = timezone.now()
        self.save(update_fields=['status', 'applied_at'])

    def close(self, status):
        self.status = status
        self.closed_at = timezone.now()
        self.save(update_fields=['status', 'closed_at'])
        self.responses.filter(status__in=[AmendmentResponse.PENDING, AmendmentResponse.ACCEPTED]).update(status=AmendmentResponse.CLOSED)


def _display(value):
    if value is True:
        return 'Yes'
    if value is False:
        return 'No'
    return '\u2014' if value in (None, '') else value


class AmendmentResponse(models.Model):
    """One quoting supplier's answer to a change request, and then the
    buyer's decision on the updated pricing if the supplier accepted."""
    PENDING = 'pending'                  # waiting on the supplier
    REJECTED = 'rejected'                # supplier won't quote on the changed RFQ
    ACCEPTED = 'accepted'                # supplier sent new pricing; waiting on the buyer
    BUYER_ACCEPTED = 'buyer_accepted'    # buyer took the new pricing: RFQ and quote updated
    BUYER_DECLINED = 'buyer_declined'    # buyer kept the old details
    CLOSED = 'closed'                    # change request withdrawn or RFQ awarded first
    STATUS_CHOICES = [
        (PENDING, 'Waiting for supplier'),
        (REJECTED, 'Supplier rejected the changes'),
        (ACCEPTED, 'New pricing sent — waiting for buyer'),
        (BUYER_ACCEPTED, 'Buyer accepted the new pricing'),
        (BUYER_DECLINED, 'Buyer kept the original details'),
        (CLOSED, 'Closed'),
    ]
    # The quote fields a supplier re-prices when accepting.
    PRICING_FIELDS = ['quote_price', 'tooling_cost', 'lead_time_value', 'lead_time_unit', 'payment_terms', 'valid_until']

    amendment = models.ForeignKey(RequirementAmendment, on_delete=models.CASCADE, related_name='responses')
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='amendment_responses')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    supplier_note = models.TextField(blank=True)
    quote_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    tooling_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    lead_time_value = models.PositiveIntegerField(blank=True, null=True)
    lead_time_unit = models.CharField(max_length=10, choices=Quote.LEAD_TIME_UNIT_CHOICES, blank=True)
    payment_terms = models.CharField(max_length=20, choices=Quote.PAYMENT_TERMS_CHOICES, blank=True)
    valid_until = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(blank=True, null=True)
    buyer_decided_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = [('amendment', 'quote')]
        ordering = ['created_at']

    def __str__(self):
        return f"Response to amendment #{self.amendment_id} for quote #{self.quote_id} ({self.status})"

    def pricing_rows(self):
        """Old vs new pricing for the buyer to compare."""
        quote = self.quote
        currency = self.amendment.requirement.quote_currency
        return [
            {'label': 'Unit price', 'old': f"{currency} {quote.quote_price}", 'new': f"{currency} {self.quote_price}"},
            {'label': 'Tooling / setup', 'old': f"{currency} {quote.tooling_cost}", 'new': f"{currency} {self.tooling_cost}"},
            {'label': 'Lead time', 'old': _lead_time(quote.lead_time_value, quote.lead_time_unit), 'new': _lead_time(self.lead_time_value, self.lead_time_unit)},
            {'label': 'Payment terms', 'old': quote.get_payment_terms_display() or '\u2014', 'new': self.get_payment_terms_display() or '\u2014'},
            {'label': 'Valid until', 'old': quote.valid_until or '\u2014', 'new': self.valid_until or '\u2014'},
        ]

    def apply_pricing(self):
        for field in self.PRICING_FIELDS:
            setattr(self.quote, field, getattr(self, field))
        self.quote.revised_at = timezone.now()
        self.quote.save()


def _lead_time(value, unit):
    return f"{value} {unit}" if value else '\u2014'


class RFQDecline(models.Model):
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='declines')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='rfq_declines')
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('requirement', 'supplier')]

    def __str__(self):
        return f"Decline: requirement #{self.requirement_id} by supplier #{self.supplier_id}"
