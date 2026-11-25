"""People in an organization: inviting them by email, accepting an
invitation, changing someone's role or teams, and deactivating them.

Plan limits (plans.erp): full users are a hard limit, counting open
invitations, so a company can't invite past it; light users can go past the
number included and are billed for the extra ones (billing.erp). After a
downgrade nobody is removed, but nobody new can be added until it fits.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts import team as marketplace_team
from core.tasks import queue_template_email
from plans import erp

from . import catalog, workspace
from .models import Invitation, Membership, Role, Team, TeamMembership

INVITATION_TTL_DAYS = 7


class PeopleError(Exception):
    """A request that can't be carried out; the message says why."""


def _bigger_plan(plan_key, field, current_limit):
    if plan_key not in erp.PLANS:
        return None
    later = erp.PLAN_ORDER[erp.PLAN_ORDER.index(plan_key) + 1:]
    return next((k for k in later if erp.PLANS[k][field] is None or erp.PLANS[k][field] > current_limit), None)


def full_user_room(company, adding=1):
    """Whether `adding` more full users (people or invitations) fit the
    plan. Returns (ok, message)."""
    usage = workspace.seat_usage(company)
    limit = usage['full_included']
    if limit is None or usage['full'] + usage['full_invited'] + adding <= limit:
        return True, ''
    bigger = _bigger_plan(company.erp_plan, 'users', limit)
    hint = f" Move to {erp.PLANS[bigger]['label']} for more, or add them as a light user." if bigger else \
        " Add them as a light user, or contact us for more."
    return False, f"Your plan includes {limit} user{'' if limit == 1 else 's'}, and they're all in use.{hint}"


def _check_role_and_teams(company, role, teams):
    if role.company_id != company.pk:
        raise PeopleError("That role belongs to another organization.")
    if any(t.company_id != company.pk for t in teams):
        raise PeopleError("That team belongs to another organization.")


def invite(company, inviter, *, email, first_name, last_name='', role, teams=()):
    """Emails an invitation to join `company` as `role`. Returns it."""
    from core.models import User

    email = (email or '').strip().lower()
    first_name = (first_name or '').strip()
    if not email or not first_name:
        raise PeopleError("Enter their email address and first name.")
    teams = list(teams)
    _check_role_and_teams(company, role, teams)
    if Membership.objects.filter(company=company, user__email__iexact=email).exists():
        raise PeopleError(f"{email} is already in this organization.")
    if Invitation.objects.filter(company=company, email__iexact=email, accepted_at__isnull=True,
                                 revoked_at__isnull=True, expires_at__gt=timezone.now()).exists():
        raise PeopleError(f"{email} already has an open invitation. Resend it instead.")
    existing = User.objects.filter(email__iexact=email).first()
    if existing is not None and existing.role != 'member':
        raise PeopleError(f"{email} is used by a MakeSetu marketplace account, which can't join an organization yet. "
                          "Invite a different address.")
    if not role.is_light:
        ok, message = full_user_room(company)
        if not ok:
            raise PeopleError(message)

    raw, token_hash = marketplace_team.new_invitation_token()
    invitation = Invitation.objects.create(
        company=company, email=email, first_name=first_name[:150], last_name=(last_name or '').strip()[:150],
        role=role, token_hash=token_hash, invited_by=inviter,
        expires_at=timezone.now() + timedelta(days=INVITATION_TTL_DAYS),
    )
    invitation.teams.set(teams)
    _send_invitation(invitation, raw)
    return invitation


def resend(invitation, by):
    """A new link (the old one stops working) and a fresh expiry."""
    if invitation.accepted_at or invitation.revoked_at:
        raise PeopleError("This invitation was already accepted or revoked.")
    raw, invitation.token_hash = marketplace_team.new_invitation_token()
    invitation.expires_at = timezone.now() + timedelta(days=INVITATION_TTL_DAYS)
    invitation.save(update_fields=['token_hash', 'expires_at'])
    _send_invitation(invitation, raw)
    return invitation


def revoke(invitation, by):
    if invitation.accepted_at:
        raise PeopleError("This invitation was already accepted.")
    if invitation.revoked_at is None:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=['revoked_at'])
    return invitation


def _send_invitation(invitation, raw_token):
    inviter = invitation.invited_by
    queue_template_email(
        invitation.email, 'emails/org_invitation_subject.txt', 'emails/org_invitation.txt',
        {
            'first_name': invitation.first_name,
            'inviter_name': (inviter.get_full_name() or inviter.email) if inviter else 'Your administrator',
            'company_name': invitation.company.name or invitation.company.legal_name,
            'role_label': invitation.role.name,
            'url': settings.SITE_URL + reverse('org-invitation-accept', kwargs={'token': raw_token}),
            'ttl_days': INVITATION_TTL_DAYS,
        },
    )


def find_invitation(raw_token):
    return Invitation.objects.select_related('company', 'role', 'invited_by').filter(
        token_hash=marketplace_team.hash_token(raw_token or '')).first()


@transaction.atomic
def accept(invitation, user):
    """`user` (whose email matches) joins the organization. Returns the
    membership. Someone who was in it before and was deactivated comes
    back with the invited role."""
    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
    if not invitation.is_open:
        raise PeopleError("This invitation has expired, was revoked, or was already used.")
    if user.email.lower() != invitation.email.lower():
        raise PeopleError(f"This invitation is for {invitation.email}.")
    membership, created = Membership.objects.get_or_create(
        user=user, company=invitation.company,
        defaults={'role': invitation.role, 'invited_by': invitation.invited_by},
    )
    if not created:
        membership.role = invitation.role
        membership.is_active = True
        membership.save()
    for team in invitation.teams.all():
        TeamMembership.objects.get_or_create(team=team, user=user)
    invitation.accepted_at = timezone.now()
    invitation.accepted_by = user
    invitation.save(update_fields=['accepted_at', 'accepted_by'])
    return membership


def _active_admins(company):
    return Membership.objects.filter(company=company, is_active=True, user__is_active=True, role__key=catalog.ORG_ADMIN)


@transaction.atomic
def change_member(membership, by, *, role=None, is_active=None, teams=None):
    """Changes someone's role, whether they're active, and/or their teams.
    An organization always keeps at least one active Org Admin."""
    membership = Membership.objects.select_for_update().select_related('role', 'company').get(pk=membership.pk)
    company = membership.company
    new_role = role or membership.role
    new_active = membership.is_active if is_active is None else bool(is_active)
    if role is not None:
        _check_role_and_teams(company, role, [])

    was_admin = membership.is_active and membership.role.key == catalog.ORG_ADMIN
    stays_admin = new_active and new_role.key == catalog.ORG_ADMIN
    if was_admin and not stays_admin and not _active_admins(company).exclude(pk=membership.pk).exists():
        raise PeopleError("Every organization needs at least one active Org Admin. Make someone else Org Admin first.")

    takes_full_seat = new_active and not new_role.is_light
    had_full_seat = membership.is_active and not membership.role.is_light
    if takes_full_seat and not had_full_seat:
        ok, message = full_user_room(company)
        if not ok:
            raise PeopleError(message)

    membership.role = new_role
    membership.is_active = new_active
    membership.save()
    if teams is not None:
        teams = list(teams)
        if any(t.company_id != company.pk for t in teams):
            raise PeopleError("That team belongs to another organization.")
        TeamMembership.objects.filter(user=membership.user, team__company=company).exclude(team__in=teams).delete()
        for team in teams:
            TeamMembership.objects.get_or_create(team=team, user=membership.user)
        Team.objects.filter(company=company, head=membership.user).exclude(pk__in=[t.pk for t in teams]).update(head=None)
    return membership


def roles_for(company):
    return Role.objects.filter(company=company).prefetch_related('permissions')
