from django.apps import AppConfig


class JobworkConfig(AppConfig):
    """Job work (CGST Act s.143): goods sent to a job worker on a challan
    without tax, tracked while they're away, and received back processed;
    reported in Form ITC-04."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "jobwork"
    verbose_name = "Job work"
