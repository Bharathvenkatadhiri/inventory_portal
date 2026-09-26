from django.conf import settings
from django.core.cache import cache
from django.db import models

PUBLIC_FEEDBACK_CACHE_KEY = 'portal_feedback_public'

# One list for both "most useful" and "needs improvement", so the staff
# summary can put the two side by side per feature.
PORTAL_FEATURES = [
    ('rfq_posting', 'Posting RFQs'),
    ('supplier_matching', 'Finding manufacturers'),
    ('quote_comparison', 'Comparing quotes'),
    ('messaging', 'Messaging'),
    ('order_tracking', 'Order & production tracking'),
    ('qc_documents', 'QC checklist & documents'),
    ('notifications', 'Notifications'),
    ('pricing_plans', 'Pricing & plans'),
    ('ease_of_use', 'Ease of use'),
    ('mobile', 'Mobile experience'),
]


class PortalFeedback(models.Model):
    """A buyer's or manufacturer's rating of ManufactureHub itself. One per
    user; submitting again updates it. The comment is only shown publicly
    when the user allowed it, it isn't hidden by staff, and it rates 4+."""
    RATING_CHOICES = [(n, str(n)) for n in range(1, 6)]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='portal_feedback')
    role = models.CharField(max_length=20)  # the user's role when they last submitted
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    useful_features = models.JSONField(default=list, blank=True)
    improvement_areas = models.JSONField(default=list, blank=True)
    improvement_note = models.TextField(max_length=1000, blank=True)  # staff only, never public
    comment = models.TextField(max_length=500, blank=True)
    allow_public = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Portal feedback from {self.user}: {self.rating}/5"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(PUBLIC_FEEDBACK_CACHE_KEY)

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        cache.delete(PUBLIC_FEEDBACK_CACHE_KEY)
        return result
