import logging

import boto3
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class SESEmailBackend(BaseEmailBackend):
    """Django email backend that sends messages through Amazon SES."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.client = boto3.client(
            "ses",
            region_name=settings.AWS_SES_REGION,
        )

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        sent = 0

        for message in email_messages:
            if self._send(message):
                sent += 1

        return sent

    def _send(self, message):
        if not message.recipients():
            return False

        try:
            body = {
                "Text": {
                    "Data": message.body,
                    "Charset": "UTF-8",
                }
            }

            html_parts = [
                alternative
                for alternative in message.alternatives
                if alternative[1] == "text/html"
            ]

            if html_parts:
                body["Html"] = {
                    "Data": html_parts[0][0],
                    "Charset": "UTF-8",
                }

            self.client.send_email(
                Source=message.from_email,
                Destination={
                    "ToAddresses": message.to,
                    "CcAddresses": message.cc,
                    "BccAddresses": message.bcc,
                },
                Message={
                    "Subject": {
                        "Data": message.subject,
                        "Charset": "UTF-8",
                    },
                    "Body": body,
                },
                ReplyToAddresses=message.reply_to,
            )

            return True

        except Exception:
            logger.exception(
                "Failed to send email through Amazon SES to %s",
                message.recipients(),
            )

            if not self.fail_silently:
                raise

            return False
