"""Creating an organization and its first member."""
from django.db import transaction

from .catalog import DEFAULT_ROLES, ORG_ADMIN
from .models import Address, Membership, Permission, Role


def pan_from_gstin(gstin):
    """A GSTIN is state code (2) + PAN (10) + entity number, 'Z', checksum."""
    return (gstin or '')[2:12].upper()


def create_default_roles(company):
    """The roles in organizations.catalog.DEFAULT_ROLES, for a new organization."""
    permissions = {p.code: p for p in Permission.objects.all()}
    roles = {}
    for key, (name, description, codes, user_type) in DEFAULT_ROLES.items():
        role, _ = Role.objects.get_or_create(
            company=company, key=key,
            defaults={'name': name, 'description': description, 'is_system': True, 'user_type': user_type},
        )
        role.permissions.set([permissions[c] for c in codes if c in permissions])
        roles[key] = role
    return roles


@transaction.atomic
def set_up_organization(company, owner, *, buying, selling, plan):
    """Turns a (GST-verified, saved) Company into an ERP organization: the
    sides it uses, its plan, default roles, its registered address from GST,
    and `owner` as its Org Admin. Returns the owner's Membership."""
    company.pan = pan_from_gstin(company.gstin)
    company.buying_enabled = bool(buying)
    company.selling_enabled = bool(selling)
    company.erp_plan = plan
    company.save(update_fields=['pan', 'buying_enabled', 'selling_enabled', 'erp_plan', 'updated_at'])

    roles = create_default_roles(company)
    if company.principal_address and not company.addresses.filter(kind=Address.Kind.REGISTERED).exists():
        Address.objects.create(
            company=company, kind=Address.Kind.REGISTERED, line1=company.principal_address[:255],
            city=company.city[:100], state=company.state[:100], state_code=company.state_code[:2],
            pincode=company.pincode[:10], is_default=True,
        )
    from billing import erp as erp_billing
    erp_billing.start_trial(company, plan)
    return Membership.objects.create(user=owner, company=company, role=roles[ORG_ADMIN])
