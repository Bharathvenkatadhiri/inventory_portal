"""Background jobs shared by every app."""
import logging

from celery import shared_task

from .emails import send_template_email

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_template_email_task(self, to_email, subject_template, text_template, context, html_template=None):
    """core.emails.send_template_email as a job, retried if sending fails.
    `context` must be JSON-serialisable."""
    sent = send_template_email(to_email, subject_template, text_template, context, html_template=html_template)
    if not sent and not self.request.is_eager:
        raise self.retry()
    return sent


def queue_template_email(to_email, subject_template, text_template, context, html_template=None):
    """Sends the email from a worker, so the request doesn't wait on SMTP.
    If the broker can't be reached the email is sent inline instead: an
    OTP that never arrives is worse than a slower page. Like
    send_template_email, it never raises."""
    try:
        send_template_email_task.delay(to_email, subject_template, text_template, context, html_template)
    except Exception:
        logger.exception("Couldn't queue email (%s); sending it inline", text_template)
        send_template_email(to_email, subject_template, text_template, context, html_template=html_template)
