import hashlib
import hmac
import logging

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from django.contrib.auth.decorators import login_not_required
from django.utils.decorators import method_decorator

logger = logging.getLogger(__name__)


@method_decorator(login_not_required, name='dispatch')
class RazorpayWebhookView(APIView):
    """Verifies and accepts Razorpay webhook events.

    This is a stub: it verifies the signature and returns 200 so Razorpay
    considers the webhook delivered. Actual payment record creation/updates
    are a later increment.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        signature = request.headers.get('X-Razorpay-Signature', '')
        body = request.body

        secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not secret:
            logger.error("Razorpay webhook received but RAZORPAY_WEBHOOK_SECRET is not configured")
            return Response({'detail': 'Webhook secret not configured'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        expected_signature = hmac.new(
            key=secret.encode('utf-8'),
            msg=body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            logger.warning(
                "Razorpay webhook rejected: signature mismatch (remote_addr=%s)",
                request.META.get('REMOTE_ADDR'),
            )
            return Response({'detail': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)

        logger.info("Razorpay webhook accepted (event=%s)", request.data.get('event', 'unknown'))
        # TODO: parse request.data and update/create the corresponding
        # Payment record once the payment-creation flow exists.
        return Response({'detail': 'ok'}, status=status.HTTP_200_OK)
