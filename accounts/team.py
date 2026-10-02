"""Company accounts with a team: one manager, plus supervisors and users.

Every question of the form "which company is this user acting for" or "may
this user do X" goes through here, never through `profile.user == user`.

- manager: whoever owns the company's ConsumerProfile/ManufacturerProfile
  (the person who registered it). Can do everything, including team,
  billing and company details.
- supervisor (TeamMember.SUPERVISOR): acts without approval, approves
  users' work, manages users, sees the team's activity.
- member, shown as "User" (TeamMember.MEMBER): day-to-day work; sending
  an RFQ to suppliers, submitting/revising a quote, awarding a quote and
  confirming a payment wait for a supervisor's or the manager's approval
  (marketplace.approvals).

A user with no company yet (mid-registration, staff) is a team of one, so
every check below falls back to plain "is it me".
"""
import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

MANAGER = 'manager'
SUPERVISOR = 'supervisor'
MEMBER = 'member'
ROLE_LABELS = {MANAGER: 'Manager', SUPERVISOR: 'Supervisor', MEMBER: 'User'}

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
            info = (profile, 'supplier', MANAGER)
    else:
        profile = ConsumerProfile.objects.filter(user_id=user.pk).first()
        if profile is not None:
            info = (profile, 'buyer', MANAGER)
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


def is_manager(user):
    return team_role(user) == MANAGER


def can_approve(user):
    return team_role(user) in (MANAGER, SUPERVISOR)


def needs_approval(user):
    return team_role(user) == MEMBER


def can_invite(user, role):
    """Managers add supervisors and users; supervisors add users."""
    actor = team_role(user)
    return actor == MANAGER or (actor == SUPERVISOR and role == MEMBER)


def can_manage_member(user, row):
    """Deactivate/reactivate a TeamMember or revoke a TeamInvitation: the
    manager for anyone, a supervisor for users only. Nobody manages
    themselves here."""
    if getattr(row, 'user_id', None) == user.pk or not belongs_to(row, company(user)):
        return False
    actor = team_role(user)
    return actor == MANAGER or (actor == SUPERVISOR and row.role == MEMBER)


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
    """Ids of everyone on the user's company account (manager included,
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


def manager_user(user):
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


def seat_limit(profile):
    """Seats on the manager's plan (None = unlimited)."""
    from core.settings import subscription_plan_details
    from .models import SubscriptionPlan
    plan = SubscriptionPlan.objects.filter(user_profile=profile.user, is_active=True).first()
    details = subscription_plan_details.get(plan.plan_type if plan else 'basic') or subscription_plan_details['basic']
    return details.get('team_seats')


def seats_used(profile):
    active_members = members_of(profile).filter(user__is_active=True).count()
    return 1 + active_members + open_invitations_of(profile).count()


def has_free_seat(profile):
    limit = seat_limit(profile)
    return limit is None or seats_used(profile) < limit


def approvers(profile):
    """Active users who can approve for this company: the manager and its
    supervisors."""
    from .models import TeamMember
    users = [profile.user] if profile.user.is_active else []
    users += [
        m.user for m in TeamMember.objects.filter(**company_filter(profile), role=TeamMember.SUPERVISOR, user__is_active=True)
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
