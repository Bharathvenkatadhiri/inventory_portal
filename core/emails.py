"""Sends templated transactional email: registration OTPs (accounts.otp)
and marketplace lifecycle notifications (marketplace.emails).

Every email goes through send_template_email, which never raises — the
action it's attached to (posting an RFQ, awarding a quote, advancing an
order) must complete even if SMTP is down or misconfigured, the same
principle as core.audit.record. Failures are logged instead.

There's no task queue in this project, so every send happens inline in
the request. That's fine for a single recipient; issue_new_rfq_emails
fans out to every matching manufacturer and is the one place volume
could matter — see its own docstring in marketplace/emails.py.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_template_email(to_email, subject_template, text_template, context, html_template=None):
    """Renders `subject_template` (one line) and `text_template` with
    `context`, optionally attaching `html_template` as the HTML
    alternative, and sends it to `to_email`. Returns True/False instead of
    raising, so a call site never has to guard it."""
    if not to_email:
        logger.warning("send_template_email: no recipient for %s", text_template)
        return False
    try:
        subject = " ".join(render_to_string(subject_template, context).split())
        body = render_to_string(text_template, context)
        message = EmailMultiAlternatives(
            subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[to_email],
        )
        if html_template:
            try:
                message.attach_alternative(render_to_string(html_template, context), "text/html")
            except TemplateDoesNotExist:
                pass
        message.send()
        return True
    except Exception:
        logger.exception("Failed to send email (%s) to %s", text_template, to_email)
        return False
