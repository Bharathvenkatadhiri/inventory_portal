from django.conf import settings
from django.db import models
from django_fsm import FSMField, transition

from accounts.models import ManufacturerProfile, ConsumerProfile


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
    id = models.AutoField(primary_key=True)
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='quote')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='quotes')
    quote_price = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=200, blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True, choices=QUOTE_STATUS)
    is_selected = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Quote #{self.id} - {self.requirement.title}"


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

    billno = models.AutoField(primary_key=True)
    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name='orders')
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='orders')
    supplier = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='orders')
    customer = models.ForeignKey(ConsumerProfile, on_delete=models.CASCADE, related_name='orders')
    status = FSMField(default='submitted', choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Order no: " + str(self.billno)

    def get_items_list(self):
        return RequirementPart.objects.filter(requirement=self.requirement_id)

    def _log(self, from_status, to_status, note=''):
        OrderStatusHistory.objects.create(order=self, from_status=from_status, to_status=to_status, note=note)

    @transition(field=status, source='submitted', target='quoted')
    def mark_quoted(self, note=''):
        self._log('submitted', 'quoted', note)

    @transition(field=status, source='quoted', target='quote_selected')
    def select_quote(self, note=''):
        self._log('quoted', 'quote_selected', note)

    @transition(field=status, source='quote_selected', target='in_production')
    def start_production(self, note=''):
        self._log('quote_selected', 'in_production', note)

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
