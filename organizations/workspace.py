"""Which organization a signed-in person is working in right now.

Every tab of a browser shares one session, so the session can't say which
organization a request is for: switching in one tab would silently move
every other tab, and a form still open in an old tab would save into the
newly chosen organization. So each tab of the web app sends the
organization it's showing in the X-Organization header, and that is what
the request works on (after checking the person is an active member). The
session only remembers the last organization chosen, as the starting point
for a tab that hasn't picked one yet.
"""
from core.api_errors import SessionConflict

from .models import Membership

SESSION_KEY = 'active_organization_id'
HEADER = 'HTTP_X_ORGANIZATION'
# Set by switch() so the rest of that request (its response) uses the new
# organization rather than the one the request's header named.
_CHOSEN_ATTR = '_active_company_id'


class OrganizationUnavailable(SessionConflict):
    """The tab's organization is one the person can't work in (any more)."""
    code = 'organization'

    def __init__(self):
        super().__init__("You're no longer a member of the organization this tab was showing. It will reload.")


def memberships(user):
    if not user.is_authenticated:
        return Membership.objects.none()
    return Membership.objects.filter(user=user, is_active=True).select_related('company', 'role')


def is_member(user):
    return memberships(user).exists()


def active_membership(request):
    """The membership for the organization this request is for: the one
    the tab named (X-Organization), else the session's last choice, else
    the person's first organization (remembered for next time), else None.

    A tab that names an organization the person isn't an active member of
    gets OrganizationUnavailable, never a quiet fallback to another
    organization: that fallback is exactly the wrong-organization save
    this header exists to prevent."""
    mine = memberships(request.user)
    requested = getattr(request, _CHOSEN_ATTR, None) or request.META.get(HEADER)
    if requested:
        try:
            company_id = int(requested)
        except (TypeError, ValueError):
            raise OrganizationUnavailable()
        chosen = mine.filter(company_id=company_id).first()
        if chosen is None:
            raise OrganizationUnavailable()
        return chosen

    chosen = mine.filter(company_id=request.session.get(SESSION_KEY)).first()
    if chosen is None:
        chosen = mine.first()
        if chosen is not None:
            request.session[SESSION_KEY] = chosen.company_id
    return chosen


def switch(request, company_id):
    """Makes `company_id` this request's organization and the session's
    default for new tabs, if the person is an active member of it. Other
    open tabs keep the organization they're showing. Returns the
    membership, or None."""
    chosen = memberships(request.user).filter(company_id=company_id).first()
    if chosen is not None:
        request.session[SESSION_KEY] = chosen.company_id
        setattr(request, _CHOSEN_ATTR, chosen.company_id)
    return chosen


def seat_usage(company):
    """Active people in `company`, counted the way plans count them: each
    person once per organization (one Membership each), however many teams
    they're in; full and light users separately. Plus teams used.

    Open invitations hold a seat of their role's type (`*_invited`): plan
    limits count them (organizations.people), billing doesn't
    (`extra_light` is people who have joined)."""
    from django.utils import timezone

    from plans import erp

    from .catalog import LIGHT
    from .models import Invitation

    active = Membership.objects.filter(company=company, is_active=True, user__is_active=True)
    light = active.filter(role__user_type=LIGHT).count()
    full = active.count() - light
    open_invites = Invitation.objects.filter(company=company, accepted_at__isnull=True, revoked_at__isnull=True,
                                             expires_at__gt=timezone.now())
    light_invited = open_invites.filter(role__user_type=LIGHT).count()
    full_invited = open_invites.count() - light_invited
    entry = erp.PLANS.get(company.erp_plan) or {}
    return {
        'full': full, 'full_invited': full_invited, 'full_included': entry.get('users'),
        'light': light, 'light_invited': light_invited, 'light_included': entry.get('light_users'),
        'extra_light': max(0, light - entry['light_users']) if entry.get('light_users') is not None else 0,
        'extra_light_monthly': erp.EXTRA_LIGHT_USER_MONTHLY,
        'teams': company.teams.count(), 'teams_included': entry.get('teams'),
    }
