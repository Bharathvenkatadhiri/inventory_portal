"""The one entry point for verifying a GSTIN. Callers never talk to a
provider directly:

    GSTVerificationService().verify(gstin)             # e.g. during sign-up
    GSTVerificationService().verify(gstin, company)    # re-check a company

Every call records a GSTVerification (success or not) and returns it.
"""
import logging

from django.utils import timezone

from gst.exceptions import GSTINNotFound, GSTInvalidResponse, GSTProviderError
from gst.models import GSTVerification

from .providers import get_gst_provider
from .providers.base import ACTIVE

logger = logging.getLogger(__name__)


class GSTVerificationService:
    def __init__(self, provider=None):
        self.provider = provider or get_gst_provider()

    def verify(self, gstin, company=None):
        gstin = gstin.strip().upper()
        try:
            result = self.provider.verify(gstin)
            self._check(result)
        except GSTINNotFound:
            return self._record(gstin, company, GSTVerification.Status.INVALID, error_code=GSTINNotFound.code)
        except GSTProviderError as exc:
            logger.warning("GST lookup failed for %s via %s: %s (%s)", gstin, self.provider.name, exc.code, exc)
            return self._record(gstin, company, GSTVerification.Status.FAILED, error_code=exc.code, error_message=str(exc))
        except Exception as exc:
            logger.exception("Unexpected error verifying %s via %s", gstin, self.provider.name)
            return self._record(gstin, company, GSTVerification.Status.FAILED, error_code=GSTProviderError.code, error_message=repr(exc))

        if result['gst_status'] == ACTIVE:
            verification = self._record(gstin, company, GSTVerification.Status.SUCCESS, result=result)
        else:
            verification = self._record(gstin, company, GSTVerification.Status.INACTIVE, result=result, error_code='GSTIN_INACTIVE')
        if company is not None:
            self.apply_to_company(verification, company)
        return verification

    @staticmethod
    def _check(result):
        if not isinstance(result, dict) or not result.get('legal_name') or not result.get('gst_status'):
            raise GSTInvalidResponse("provider returned no legal name or GST status")

    def _record(self, gstin, company, status, result=None, error_code='', error_message=''):
        result = result or {}
        return GSTVerification.objects.create(
            # A new company is linked by apply_to_company once it's saved.
            company=company if company is not None and company.pk else None,
            gstin=gstin,
            provider=self.provider.name,
            status=status,
            legal_name=result.get('legal_name', ''),
            trade_name=result.get('trade_name', ''),
            gst_status=result.get('gst_status', ''),
            registration_date=result.get('registration_date'),
            cancellation_date=result.get('cancellation_date'),
            taxpayer_type=result.get('taxpayer_type', ''),
            business_constitution=result.get('business_constitution', ''),
            principal_address=result.get('principal_address') or {},
            state=result.get('state', ''),
            state_code=result.get('state_code', ''),
            pincode=result.get('pincode', ''),
            provider_reference=result.get('provider_reference', ''),
            raw_response=result.get('raw_response') or {},
            error_code=error_code,
            error_message=error_message,
            verified_at=timezone.now() if status == GSTVerification.Status.SUCCESS else None,
        )

    @staticmethod
    def apply_to_company(verification, company):
        """Makes `company` reflect `verification`: the full GST identity when
        it succeeded, just the lapsed status when the GSTIN is no longer
        active. A failed lookup tells us nothing, so it changes nothing."""
        if verification.status == GSTVerification.Status.SUCCESS:
            company.gstin = verification.gstin
            company.legal_name = verification.legal_name
            company.trade_name = verification.trade_name
            company.gst_status = verification.gst_status
            company.gst_verified = True
            company.gst_verified_at = verification.verified_at
            company.gst_registration_date = verification.registration_date
            company.gst_cancellation_date = verification.cancellation_date
            company.taxpayer_type = verification.taxpayer_type
            company.business_constitution = verification.business_constitution
            company.principal_address = verification.address
            company.city = verification.city[:100]
            company.state = verification.state
            company.state_code = verification.state_code
            company.pincode = verification.pincode
        elif verification.status == GSTVerification.Status.INACTIVE and company.gstin == verification.gstin:
            company.gst_status = verification.gst_status
            company.gst_cancellation_date = verification.cancellation_date
            company.gst_verified = False
        else:
            return
        company.save()
        if verification.company_id != company.pk:
            verification.company = company
            verification.save(update_fields=['company'])
