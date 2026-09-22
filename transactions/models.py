from django.db import models
from inventory.models import Stock
from accounts.models import Supplier_details, Customer
#from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone

# Contains suppliers
class Supplier(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    phone = models.CharField(max_length=12, unique=True)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=50, blank=False, null=False)
    state = models.CharField(max_length=25, blank=False, null=False)
    country = models.CharField(max_length=25, blank=False, null=False)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.user)

# Contains the purchase bills made
class PurchaseBill(models.Model):
    billno = models.AutoField(primary_key=True)
    time = models.DateTimeField(auto_now=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='purchasesupplier')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return "Bill no: " + str(self.billno)
    def get_items_list(self):
        return PurchaseItem.objects.filter(billno=self)
    def get_total_price(self):
        purchaseitems = PurchaseItem.objects.filter(billno=self)
        total = sum(item.totalprice for item in purchaseitems)
        return total

# Contains the purchase stocks made
class PurchaseItem(models.Model):
    billno = models.ForeignKey(PurchaseBill, on_delete=models.CASCADE, related_name='purchasebillno')
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='purchaseitem')
    quantity = models.IntegerField(default=1)
    perprice = models.IntegerField(default=1)
    totalprice = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return "Bill no: " + str(self.billno.billno) + ", Item = " + self.stock.name

# Contains the other details in the purchases bill
class PurchaseBillDetails(models.Model):
    billno = models.ForeignKey(PurchaseBill, on_delete=models.CASCADE, related_name='purchasedetailsbillno')
    eway = models.CharField(max_length=50, blank=True, null=True)
    veh = models.CharField(max_length=50, blank=True, null=True)
    destination = models.CharField(max_length=50, blank=True, null=True)
    po = models.CharField(max_length=50, blank=True, null=True)
    cgst = models.CharField(max_length=50, blank=True, null=True)
    sgst = models.CharField(max_length=50, blank=True, null=True)
    igst = models.CharField(max_length=50, blank=True, null=True)
    cess = models.CharField(max_length=50, blank=True, null=True)
    tcs = models.CharField(max_length=50, blank=True, null=True)
    total = models.IntegerField(default=0)

    def __str__(self):
        return "Bill no: " + str(self.billno.billno)

# Contains the sale bills made
class SaleBill(models.Model):
    billno = models.AutoField(primary_key=True)
    time = models.DateTimeField(auto_now=True)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=12)
    address = models.CharField(max_length=200)
    email = models.EmailField(max_length=254)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Bill no: " + str(self.billno)

    def get_items_list(self):
        return SaleItem.objects.filter(billno=self)

    def get_total_price(self):
        saleitems = SaleItem.objects.filter(billno=self)
        total = sum(item.totalprice for item in saleitems)
        return total


# Contains the sale stocks made
class SaleItem(models.Model):
    billno = models.ForeignKey(SaleBill, on_delete=models.CASCADE, related_name='salebillno')
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='saleitem')
    quantity = models.IntegerField(default=1)
    perprice = models.IntegerField(default=1)
    totalprice = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return "Bill no: " + str(self.billno.billno) + ", Item = " + self.stock.name

# Contains the other details in the sales bill
class SaleBillDetails(models.Model):
    billno = models.ForeignKey(SaleBill, on_delete=models.CASCADE, related_name='saledetailsbillno')
    eway = models.CharField(max_length=50, blank=True, null=True)
    veh = models.CharField(max_length=50, blank=True, null=True)
    destination = models.CharField(max_length=50, blank=True, null=True)
    po = models.CharField(max_length=50, blank=True, null=True)
    cgst = models.CharField(max_length=50, blank=True, null=True)
    sgst = models.CharField(max_length=50, blank=True, null=True)
    igst = models.CharField(max_length=50, blank=True, null=True)
    cess = models.CharField(max_length=50, blank=True, null=True)
    tcs = models.CharField(max_length=50, blank=True, null=True)
    total = models.IntegerField(default=0)

    def __str__(self):
        return "Bill no: " + str(self.billno.billno)

# Contains demands
class Demand(models.Model):
    Demand_STATUS = [
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
    file = models.FileField(upload_to='demand_files/', blank=True, null=True)
    supplier_id = models.IntegerField(default=0)
    quote_id = models.IntegerField(default=0)
    status = models.CharField(max_length=50, blank=True, null=True ,choices=Demand_STATUS)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Demand #{self.id} - {self.user.first_name}"

class DemandParts(models.Model):
    technology_TYPES = [
        ('Anodizing', 'Anodizing'),
        ('Full-range turning', 'Full-range turning'),
        ('Turning', 'Turning'),
        ('Milling', 'Milling'),
    ]
    Material_TYPES = [
        ('Structural steel', 'Structural steel'),
        ('Stainless steel', 'Stainless steel'),
        ('Aluminium', 'Aluminium'),
        ('Case hardening', 'Case hardening'),
    ]
    id = models.AutoField(primary_key=True)
    demand = models.ForeignKey(Demand, on_delete=models.CASCADE)
    part_name = models.CharField(max_length=100, verbose_name='Name')
    Part_desc = models.CharField(max_length=200, blank=True, null=True)
    technology =models.CharField(max_length=50, choices=technology_TYPES)
    Material =models.CharField(max_length=50, choices=Material_TYPES)
    file = models.FileField(upload_to='media/demand_files/', blank=True, null=True)
    quantity = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Demand_parts #{self.id} - {self.demand.title}"


class Quote(models.Model):
    QUOTE_STATUS = [
        ('Approved', 'Approved'),
        ('Hold', 'Hold'),
        ('Rejected', 'Rejected'),
    ]
    id = models.AutoField(primary_key=True)
    demand = models.ForeignKey(Demand, on_delete=models.CASCADE)
    supplier = models.ForeignKey(Supplier_details , on_delete=models.CASCADE, related_name='demand_supplier')
    quote_price = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=200, blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True ,choices=QUOTE_STATUS)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Demand #{self.id} - {self.demand.title}"


class RfqBill(models.Model):
    billno = models.AutoField(primary_key=True)
    demand =  models.ForeignKey(Demand, on_delete=models.CASCADE, related_name='demand')
    quote =  models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='quote')
    supplier =  models.ForeignKey(Supplier_details, on_delete=models.CASCADE, related_name='supplier_details')
    customer =  models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='customer')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Bill no: " + str(self.billno)

    def get_items_list(self):
        return DemandParts.objects.filter(demand=self.demand.id)
