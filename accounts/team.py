"""Company accounts with a team, and what each role on it may do.

Roles are per side. A buyer company has Owner, Admin, Procurement and
Viewer; a supplier company has Owner, Admin, Sales, Operations and Viewer.
ROLE_PERMISSIONS below is the whole permission table for each side; every
role check asks `allows(user, action)`.

- owner: whoever owns the company's ConsumerProfile/ManufacturerProfile
  (the person who registered it, or who it was handed to). Not a
  TeamMember row. Only the owner manages the subscription and billing.
- every other role is a TeamMember row.

What the company's *plan* includes (features, limits) is a separate
question, answered by plans.access, which combines the two: role, then
plan feature, then usage limit.

Every question of the form "which company is this user acting for" goes
through here, never through `profile.user == user`. A user with no company
yet (mid-registration, staff) is a team of one with full rights.
"""
import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

OWNER = 'owner'
ADMIN = 'admin'
PROCUREMENT = 'procurement'
SALES = 'sales'
OPERATIONS = 'operations'
VIEWER = 'viewer'
ROLE_LABELS = {
    OWNER: 'Owner', ADMIN: 'Admin', PROCUREMENT: 'Procurement', SALES: 'Sales', OPERATIONS: 'Operations', VIEWER: 'Viewer',
}

BUYER, SUPPLIER = 'buyer', 'supplier'
# The roles a team member (not the owner) can have, per side.
MEMBER_ROLES = {BUYER: (ADMIN, PROCUREMENT, VIEWER), SUPPLIER: (ADMIN, SALES, OPERATIONS, VIEWER)}

_BUYER_ALL = {OWNER, ADMIN, PROCUREMENT, VIEWER}
_SUPPLIER_ALL = {OWNER, ADMIN, SALES, OPERATIONS, VIEWER}
_LEADS = {OWNER, ADMIN}

ROLE_PERMISSIONS = {
    BUYER: {
        'company.view': _BUYER_ALL,
        'company.edit': _LEADS,
        'team.manage': _LEADS,                       # users and roles
        'rfq.create': _LEADS | {PROCUREMENT},
        'rfq.edit': _LEADS | {PROCUREMENT},
        'quotes.manage': _LEADS | {PROCUREMENT},     # request revisions, reject, change requests
        'quote.accept': _LEADS,
        'quote.request_accept': {PROCUREMENT},       # with the plan's approval workflows
        'messages.send': _LEADS | {PROCUREMENT},
        'order.manage': _LEADS | {PROCUREMENT},
        'order.confirm_payment': _LEADS,
        'invoice.view': _BUYER_ALL,
        'invoice.manage': _LEADS | {PROCUREMENT},
        'approvals.decide': _LEADS,
        'audit.view': _LEADS,
        'analytics.view': _BUYER_ALL,
        'directory.view': _BUYER_ALL,
        'export': _BUYER_ALL,
        'subscription.manage': {OWNER},
        'company.delete': {OWNER},                   # not built yet
    },
    SUPPLIER: {
        'company.view': _SUPPLIER_ALL,
        'company.edit': _LEADS,
        'team.manage': _LEADS,
        'capabilities.manage': _LEADS | {SALES, OPERATIONS},  # capabilities, materials, machines, certifications
        'rfq.view': _SUPPLIER_ALL,
        'rfq.evaluate': _SUPPLIER_ALL,               # open details, decline
        'rfq.accept_nda': _LEADS | {SALES, OPERATIONS},
        'rfq.alerts': _LEADS | {SALES},
        'quotes.manage': _LEADS | {SALES},           # submit, edit, withdraw, templates, change requests
        'buyer_info.view': _LEADS | {SALES, OPERATIONS},  # Viewers see the buyer's company name only
        'messages.send': _LEADS | {SALES, OPERATIONS},
        'order.manage': _SUPPLIER_ALL,
        'order.production': _LEADS | {OPERATIONS, VIEWER},
        'order.dispatch': _LEADS | {OPERATIONS, VIEWER},
        'order.production_docs': _LEADS | {OPERATIONS},
        'order.quality_docs': _LEADS | {OPERATIONS, VIEWER},
        'invoice.view': _SUPPLIER_ALL,
        'invoice.manage': _SUPPLIER_ALL,             # e.g. requesting payment
        'analytics.sales': _LEADS | {SALES},
        'analytics.operations': _LEADS | {OPERATIONS, VIEWER},
        'audit.view': _LEADS | {VIEWER},
        'audit.view_own': {SALES, OPERATIONS},
        'contacts.view': _SUPPLIER_ALL,
        'export': _SUPPLIER_ALL,
        'subscription.manage': {OWNER},
        'company.delete': {OWNER},                   # not built yet
    },
}

INVITATION_TTL = timedelta(days=7)

_INFO_ATTR = '_team_info'
_IDS_ATTR = '_team_user_ids'


def _info(user):
    """(company profile, 'buyer'|'supplier', role), cached on the user object."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return (None, None, None)
    cached = getattr(user, _INFO_ATTR, None)
    if cached is not None:
        return cached
    from .models import ConsumerProfile, ManufacturerProfile, TeamMember

    info = (None, None, None)
    membership = TeamMember.objects.filter(user_id=user.pk).select_related('buyer', 'supplier').first()
    if membership is not None:
        info = (membership.buyer, 'buyer', membership.role) if membership.buyer_id else (membership.supplier, 'supplier', membership.role)
    elif getattr(user, 'role', None) == 'manufacturer':
        profile = ManufacturerProfile.objects.filter(user_id=user.pk).first()
        if profile is not None:
            info = (profile, 'supplier', OWNER)
    else:
        profile = ConsumerProfile.objects.filter(user_id=user.pk).first()
        if profile is not None:
            info = (profile, 'buyer', OWNER)
    setattr(user, _INFO_ATTR, info)
    return info


def forget(user):
    """Drop the cached answers after this user's team or role changed."""
    for attr in (_INFO_ATTR, _IDS_ATTR):
        if hasattr(user, attr):
            delattr(user, attr)


def company(user):
    return _info(user)[0]


def company_kind(user):
    return _info(user)[1]


def buyer_profile(user):
    profile, kind, _ = _info(user)
    return profile if kind == 'buyer' else None


def supplier_profile(user):
    profile, kind, _ = _info(user)
    return profile if kind == 'supplier' else None


def team_role(user):
    return _info(user)[2]


def role_label(user):
    return ROLE_LABELS.get(team_role(user), '')


# --- What each role may do (ROLE_PERMISSIONS) ------------------------------

def allows(user, action):
    """Whether the user's role on their company may do `action`. Someone
    with no company (mid-registration, staff) is a team of one."""
    profile, side, role = _info(user)
    if profile is None:
        return True
    return role in ROLE_PERMISSIONS[side].get(action, ())


def is_owner(user):
    return team_role(user) == OWNER


def is_viewer(user):
    return team_role(user) == VIEWER


def member_roles(user_or_profile):
    """The roles a team member can be given on this company's side."""
    from .models import ConsumerProfile
    profile = user_or_profile if hasattr(user_or_profile, 'team_members') else company(user_or_profile)
    return MEMBER_ROLES[BUYER if isinstance(profile, ConsumerProfile) else SUPPLIER]


def can_manage_company(user):
    """Manage users and roles, see the full activity log, decide approval
    requests: the owner and admins."""
    return allows(user, 'team.manage')


def can_manage_subscription(user):
    """Plan, billing and payment for MakeSetu itself: the owner only."""
    return allows(user, 'subscription.manage')


def can_invite(user, role):
    return allows(user, 'team.manage') and role in member_roles(user)


def can_manage_member(user, row):
    """Change the role of, deactivate/reactivate a TeamMember, or revoke a
    TeamInvitation: the owner and admins, for anyone on the team. The owner
    isn't a row, so can't be managed here; nobody manages themselves."""
    if getattr(row, 'user_id', None) == user.pk or not belongs_to(row, company(user)):
        return False
    return team_role(user) in (OWNER, ADMIN)


def belongs_to(row, profile):
    """Whether a row with buyer/supplier FKs (TeamMember, TeamInvitation,
    TeamActivity, ApprovalRequest) is `profile`'s. Buyer and supplier
    profiles have separate id sequences, so the side matters."""
    from .models import ConsumerProfile
    if profile is None:
        return False
    if isinstance(profile, ConsumerProfile):
        return row.buyer_id == profile.pk
    return row.supplier_id == profile.pk


def team_user_ids(user):
    """Ids of everyone on the user's company account (owner included,
    deactivated members too — their RFQs and quotes stay the company's)."""
    cached = getattr(user, _IDS_ATTR, None)
    if cached is not None:
        return cached
    profile = company(user)
    if profile is None:
        ids = frozenset({user.pk})
    else:
        ids = frozenset([profile.user_id, *profile.team_members.values_list('user_id', flat=True)])
    setattr(user, _IDS_ATTR, ids)
    return ids


def is_teammate(user, other):
    """Whether `other` (a User or user id) is on the same company account
    as `user` — including `user` themselves."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    other_id = getattr(other, 'pk', other)
    return other_id is not None and other_id in team_user_ids(user)


def owner_user(user):
    """The account holding the company's subscription plan."""
    profile = company(user)
    return profile.user if profile is not None else user


def company_filter(profile):
    """Keyword args selecting rows (TeamMember, TeamInvitation,
    TeamActivity, ApprovalRequest) that belong to `profile`."""
    from .models import ConsumerProfile
    return {'buyer': profile} if isinstance(profile, ConsumerProfile) else {'supplier': profile}


def members_of(profile):
    from .models import TeamMember
    return TeamMember.objects.filter(**company_filter(profile)).select_related('user', 'invited_by')


def open_invitations_of(profile):
    from .models import TeamInvitation
    return TeamInvitation.objects.filter(
        **company_filter(profile), accepted_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now(),
    )


def users_counted(profile):
    """Everyone a plan's user limit counts: the owner, every active team
    member whatever their role (viewers too), and open invitations."""
    from .models import TeamMember
    active_members = TeamMember.objects.filter(**company_filter(profile), user__is_active=True).count()
    return int(profile.user.is_active) + active_members + open_invitations_of(profile).count()


def no_room(profile):
    """Why the plan has no room for one more person — '' if it has."""
    from plans import access, catalog
    if access.has_room(profile, catalog.USERS):
        return ''
    limit = access.limit(profile, catalog.USERS)
    return (f"Your plan includes {limit} users and they're all in use (open invitations count too). "
            "Upgrade the plan, deactivate someone or revoke an invitation first.")


def approvers(profile):
    """Active users who can approve for this company: the owner and its
    admins."""
    from .models import TeamMember
    users = [profile.user] if profile.user.is_active else []
    users += [
        m.user for m in TeamMember.objects.filter(**company_filter(profile), role=TeamMember.ADMIN, user__is_active=True)
        .select_related('user')
    ]
    return users


def new_invitation_token():
    """(raw token for the link, hash to store)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def log(user, action, summary, url='', profile=None):
    """Records one entry in the company's activity log."""
    from .models import TeamActivity
    profile = profile or company(user)
    if profile is None:
        return None
    name = (user.get_full_name() or user.email) if user is not None else 'System'
    return TeamActivity.objects.create(
        **company_filter(profile), actor=user, actor_name=name[:200], action=action[:40], summary=summary[:300], url=url[:300],
    )
