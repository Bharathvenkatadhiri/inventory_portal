"""The one permission check for ERP actions. For "create a PO to company B":

1. the user's role in their active organization has po.create;
2. that organization has switched on the side the permission belongs to
   (buying for purchase permissions, selling for sales ones) and its plan
   includes the feature;
3. if another company is involved, an active relationship exists in the
   right direction: this organization the buyer and B the supplier for a
   purchase, the other way round for a sale.
"""
from dataclasses import dataclass

from plans import erp

from . import catalog
from .models import BusinessRelationship, Membership


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ''

    def __bool__(self):
        return self.allowed


ALLOWED = Decision(True)


def check(membership, code, counterparty=None):
    """Whether `membership` may do `code` (with `counterparty`, a Company,
    if the action involves one). Returns a Decision whose reason says why
    not, for the page to show."""
    if code not in catalog.PERMISSIONS:
        return Decision(False, f"Unknown permission {code!r}.")
    if membership is None or not membership.is_active or not membership.user.is_active:
        return Decision(False, "You're not an active member of this organization.")
    if not membership.role.permissions.filter(code=code).exists():
        return Decision(False, f"Your role ({membership.role.name}) doesn't allow this.")
    if membership.role.is_light and code not in catalog.LIGHT_PERMISSIONS:
        return Decision(False, "Light users can't do this; it needs a full user.")

    company = membership.company
    side = catalog.side_of(code)
    if side == catalog.BUYING and not company.buying_enabled:
        return Decision(False, "Buying isn't switched on for this organization.")
    if side == catalog.SELLING and not company.selling_enabled:
        return Decision(False, "Selling isn't switched on for this organization.")

    feature = catalog.feature_of(code)
    if not erp.includes(company.erp_plan, feature):
        needed = erp.first_plan_with(feature)
        return Decision(False, f"This needs the {erp.PLANS[needed]['label']} plan." if needed else "Not on your plan.")

    if counterparty is not None:
        if side is None:
            return Decision(False, f"{code} isn't done with another company.")
        buyer, supplier = (company, counterparty) if side == catalog.BUYING else (counterparty, company)
        if not BusinessRelationship.objects.filter(
            buyer=buyer, supplier=supplier, status=BusinessRelationship.Status.ACTIVE,
        ).exists():
            other = 'supplier' if side == catalog.BUYING else 'customer'
            return Decision(False, f"{counterparty} isn't an active {other} of yours yet.")
    return ALLOWED


def can(user, company, code, counterparty=None):
    membership = Membership.objects.filter(user=user, company=company).select_related('role', 'company', 'user').first()
    return check(membership, code, counterparty)
