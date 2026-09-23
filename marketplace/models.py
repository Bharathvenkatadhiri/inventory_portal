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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Quote #{self.id} - {self.requirement.title}"

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Order no: " + str(self.billno)

    def get_items_list(self):
        return RequirementPart.objects.filter(requirement=self.requirement_id)

    def _log(self, from_status, to_status, note=''):
        OrderStatusHistory.objects.create(order=self, from_status=from_status, to_status=to_status, note=note)

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
        ProductionStageHistory.objects.create(order=self, from_stage=from_stage, to_stage=next_stage, note=note)
        return True

    def _seed_qc_checklist(self):
        for label in QC_CHECKLIST_ITEMS:
            QCChecklistItem.objects.get_or_create(order=self, label=label)

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


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=30, blank=True, null=True)
    to_status = models.CharField(max_length=30)
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return f"Order #{self.order_id}: {self.from_status} -> {self.to_status}"


class ProductionStageHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='production_stage_history')
    from_stage = models.CharField(max_length=20, blank=True, null=True)
    to_stage = models.CharField(max_length=20)
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return f"Order #{self.order_id}: {self.from_stage} -> {self.to_stage}"


# Fixed, not manufacturer-configurable — matches the QC checklist shown in
# the manufacturer dashboard mockup. Seeded per-order in Order.start_production().
QC_CHECKLIST_ITEMS = [
    "First article inspection passed",
    "Dimensional report attached",
    "Material certification verified",
    "Visual / cosmetic inspection passed",
    "Packaging inspected",
]


class QCChecklistItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='qc_items')
    label = models.CharField(max_length=150)
    is_checked = models.BooleanField(default=False)
    checked_at = models.DateTimeField(blank=True, null=True)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.label} ({'checked' if self.is_checked else 'open'})"


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


class RequirementQuestion(models.Model):
    """Buyer Q&A, scoped per-manufacturer so competing quoters can't see
    each other's questions — matches the "answers are shared with every
    manufacturer quoting on this RFQ" behaviour meaning shared with THAT
    manufacturer's own thread, not across manufacturers."""
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='questions')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='requirement_questions')
    asked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    question = models.TextField()
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Q on requirement #{self.requirement_id}: {self.question[:40]}"


class RFQDecline(models.Model):
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='declines')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='rfq_declines')
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('requirement', 'supplier')]

    def __str__(self):
        return f"Decline: requirement #{self.requirement_id} by supplier #{self.supplier_id}"
