"""TDS deposits. The TDS itself is on each purchase bill
(PurchaseBill.tds_*); a deposit records paying a month's TDS to the
government with challan ITNS 281."""
from django.conf import settings
from django.db import models


class TdsDeposit(models.Model):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='tds_deposits')
    # The month the TDS was deducted in (first of the month).
    period = models.DateField()
    section = models.CharField(max_length=12)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    interest = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # for late deposit
    deposited_on = models.DateField()
    bsr_code = models.CharField('BSR code', max_length=7)
    challan_serial = models.CharField(max_length=5)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period', 'section']
        constraints = [models.UniqueConstraint(fields=['company', 'bsr_code', 'challan_serial', 'deposited_on', 'section'],
                                               name='tds_challan_entered_once')]

    def __str__(self):
        return f"{self.section} {self.period:%b %Y}: {self.amount}"
