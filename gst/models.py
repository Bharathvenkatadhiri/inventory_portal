from django.db import models

USER_MESSAGES = {
    'GSTIN_NOT_FOUND': "We couldn't find this GSTIN with GSTN. Please check and try again.",
    'GSTIN_INACTIVE': "This GSTIN's registration is not active, so it can't be used to register.",
    'GST_PROVIDER_TIMEOUT': "GST verification is taking too long to respond. Please try again shortly.",
    'GST_PROVIDER_UNAVAILABLE': "GST verification is temporarily unavailable. Please try again shortly.",
    'GST_PROVIDER_RATE_LIMITED': "Too many verification requests right now. Please try again in a minute.",
    'GST_PROVIDER_INVALID_RESPONSE': "We received an unexpected response while verifying this GSTIN. Please try again.",
    'GST_PROVIDER_ERROR': "We couldn't verify this GSTIN right now. Please try again.",
}


class GSTVerification(models.Model):
    """One GSTIN lookup and what the provider said, kept as an audit trail.
    accounts.Company holds the company's current GST state; these rows hold
    how it got there (and failed attempts). Written only by
    gst.services.verification.GSTVerificationService."""

    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'      # found, and the registration is active
        INVALID = 'INVALID', 'Invalid'      # GSTN has no such GSTIN
        INACTIVE = 'INACTIVE', 'Inactive'   # found, but cancelled/suspended/...
        FAILED = 'FAILED', 'Failed'         # the lookup itself failed

    company = models.ForeignKey(
        'accounts.Company', on_delete=models.CASCADE, related_name='gst_verifications', null=True, blank=True,
    )
    gstin = models.CharField(max_length=15, db_index=True)
    provider = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=Status.choices)
    legal_name = models.CharField(max_length=255, blank=True)
    trade_name = models.CharField(max_length=255, blank=True)
    gst_status = models.CharField(max_length=50, blank=True)
    registration_date = models.DateField(null=True, blank=True)
    cancellation_date = models.DateField(null=True, blank=True)
    taxpayer_type = models.CharField(max_length=100, blank=True)
    business_constitution = models.CharField(max_length=100, blank=True)
    principal_address = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=10, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    provider_reference = models.CharField(max_length=255, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.gstin} - {self.status}"

    @property
    def is_success(self):
        return self.status == self.Status.SUCCESS

    @property
    def address(self):
        return (self.principal_address or {}).get('address', '')

    @property
    def city(self):
        return (self.principal_address or {}).get('city', '')

    @property
    def user_message(self):
        if self.is_success:
            return "GSTIN verified."
        return USER_MESSAGES.get(self.error_code, USER_MESSAGES['GST_PROVIDER_ERROR'])

    def as_payload(self):
        """What the API and the registration page show. Never includes
        raw_response or the technical error_message."""
        return {
            'success': self.is_success,
            'code': self.error_code or None,
            'message': self.user_message,
            'gstin': self.gstin,
            'status': self.status,
            'legal_name': self.legal_name,
            'trade_name': self.trade_name,
            'gst_status': self.gst_status,
            'registration_date': self.registration_date.isoformat() if self.registration_date else None,
            'taxpayer_type': self.taxpayer_type,
            'business_constitution': self.business_constitution,
            'principal_address': self.address,
            'state': self.state,
            'state_code': self.state_code,
            'pincode': self.pincode,
        }
