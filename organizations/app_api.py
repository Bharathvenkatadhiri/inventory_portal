"""/api/v1 endpoints for the signed-in ERP app (frontend/apps/web): the
person and their organizations, company settings, people and invitations,
roles, teams and partners. Billing is in billing/api.py.

Every endpoint works on the organization the person is working in
(organizations.workspace). Changes need the matching permission
(organizations.access) and a subscription that isn't lapsed (billing.erp):
a lapsed organization can read everything and change nothing but its plan.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from ninja import Router, Schema
from ninja.errors import HttpError

from billing import erp as erp_billing
from plans import erp

from . import access, catalog, partners, people, workspace
from .models import Address, BankAccount, Invitation, Membership, Permission, Role, Team

router = Router()

LAPSED_MESSAGE = "Your subscription has lapsed, so nothing can be changed. Choose a plan in Settings → Billing to continue."


def membership_for(request, perm=None, write=False):
    """The active membership, after checking `perm` and, for a change, that
    the subscription allows changes. Raises HttpError otherwise."""
    membership = workspace.active_membership(request)
    if membership is None:
        raise HttpError(403, "You aren't an active member of any organization.")
    if perm is not None:
        decision = access.check(membership, perm)
        if not decision:
            raise HttpError(403, decision.reason)
    if write and not erp_billing.writes_allowed(membership.company):
        raise HttpError(402, LAPSED_MESSAGE)
    return membership


def _bad_request(exc):
    if isinstance(exc, ValidationError):
        raise HttpError(400, ' '.join(exc.messages))
    raise HttpError(400, str(exc))


# --- Schemas --------------------------------------------------------------------

class UserOut(Schema):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str


class MembershipOut(Schema):
    company_id: int
    company_name: str
    role_name: str
    role_type: str


class RoleRef(Schema):
    id: int
    key: str
    name: str
    user_type: str


class CompanyOut(Schema):
    id: int
    name: str
    legal_name: str
    trade_name: str
    gstin: str
    pan: str
    gst_status: str
    principal_address: str
    city: str
    state: str
    state_code: str
    pincode: str
    buying_enabled: bool
    selling_enabled: bool
    findable: bool
    plan: str
    plan_label: str


class SeatsOut(Schema):
    full: int
    full_invited: int
    full_included: Optional[int]
    light: int
    light_invited: int
    light_included: Optional[int]
    extra_light: int
    extra_light_monthly: int
    teams: int
    teams_included: Optional[int]


class SubscriptionOut(Schema):
    plan: str
    plan_label: str
    status: str
    entitled: bool
    trial_ends_at: Optional[datetime]
    current_period_start: Optional[datetime]
    expires_at: Optional[datetime]
    grace_until: Optional[datetime]
    auto_renew: bool
    scheduled_plan: Optional[str]
    days_left: Optional[int]
    price: str
    next_amount: str
    next_plan_amount: str
    next_extra_light_users: int


class ActiveOut(Schema):
    company: CompanyOut
    role: RoleRef
    granted: List[str]
    allowed: List[str]
    subscription: SubscriptionOut
    seats: SeatsOut


class MeOut(Schema):
    user: UserOut
    memberships: List[MembershipOut]
    active: Optional[ActiveOut]


class MeIn(Schema):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None


class SwitchIn(Schema):
    company_id: int


def company_out(company):
    plan = erp.PLANS.get(company.erp_plan)
    return {
        'id': company.pk, 'name': company.name or company.legal_name, 'legal_name': company.legal_name,
        'trade_name': company.trade_name, 'gstin': company.gstin or '', 'pan': company.pan,
        'gst_status': company.gst_status, 'principal_address': company.principal_address, 'city': company.city,
        'state': company.state, 'state_code': company.state_code, 'pincode': company.pincode,
        'buying_enabled': company.buying_enabled, 'selling_enabled': company.selling_enabled,
        'findable': company.findable, 'plan': company.erp_plan, 'plan_label': plan['label'] if plan else '',
    }


def subscription_out(company):
    data = erp_billing.summary(company)
    for key in ('price', 'next_amount', 'next_plan_amount'):
        data[key] = str(data[key])
    return data


def _role_ref(role):
    return {'id': role.pk, 'key': role.key, 'name': role.name, 'user_type': role.user_type}


def me_out(request):
    user = request.user
    active = workspace.active_membership(request)
    data = {
        'user': {'id': user.pk, 'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email,
                 'phone': user.phone},
        'memberships': [
            {'company_id': m.company_id, 'company_name': m.company.name or m.company.legal_name,
             'role_name': m.role.name, 'role_type': m.role.user_type}
            for m in workspace.memberships(user)
        ],
        'active': None,
    }
    if active is not None:
        granted = sorted(active.role.permissions.values_list('code', flat=True))
        data['active'] = {
            'company': company_out(active.company),
            'role': _role_ref(active.role),
            'granted': granted,
            'allowed': [code for code in granted if code in catalog.PERMISSIONS and access.check(active, code)],
            'subscription': subscription_out(active.company),
            'seats': workspace.seat_usage(active.company),
        }
    return data


# --- Me -------------------------------------------------------------------------

@router.get('/me', response=MeOut, tags=['me'])
def get_me(request):
    return me_out(request)


@router.patch('/me', response=UserOut, tags=['me'])
def update_me(request, payload: MeIn):
    user = request.user
    changes = payload.dict(exclude_unset=True)
    for field in ('first_name', 'last_name'):
        if field in changes:
            value = ' '.join((changes[field] or '').split())[:150]
            if field == 'first_name' and not value:
                raise HttpError(400, "Enter your first name.")
            setattr(user, field, value)
    if 'phone' in changes:
        phone = ''.join(ch for ch in (changes['phone'] or '') if ch.isdigit() or ch == '+')
        if phone and not (10 <= len(phone.lstrip('+')) <= 13):
            raise HttpError(400, "Enter a valid mobile number, e.g. 98765 43210.")
        user.phone = phone
    user.save(update_fields=['first_name', 'last_name', 'phone'])
    return {'id': user.pk, 'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email,
            'phone': user.phone}


@router.post('/me/active-organization', response=MeOut, tags=['me'])
def switch_organization(request, payload: SwitchIn):
    if workspace.switch(request, payload.company_id) is None:
        raise HttpError(404, "You aren't an active member of that organization.")
    return me_out(request)


# --- Organization -----------------------------------------------------------------

class OrganizationIn(Schema):
    name: Optional[str] = None
    findable: Optional[bool] = None
    buying_enabled: Optional[bool] = None
    selling_enabled: Optional[bool] = None


@router.get('/organization', response=CompanyOut, tags=['organization'])
def get_organization(request):
    return company_out(membership_for(request).company)


@router.patch('/organization', response=CompanyOut, tags=['organization'])
def update_organization(request, payload: OrganizationIn):
    company = membership_for(request, 'org.manage', write=True).company
    changes = payload.dict(exclude_unset=True)
    if 'name' in changes:
        name = ' '.join((changes['name'] or '').split())
        if not name:
            raise HttpError(400, "Enter the name your company goes by.")
        company.name = name[:255]
    for field in ('findable', 'buying_enabled', 'selling_enabled'):
        if field in changes and changes[field] is not None:
            setattr(company, field, changes[field])
    if not company.buying_enabled and not company.selling_enabled:
        raise HttpError(400, "Keep buying, selling or both switched on.")
    company.save()
    return company_out(company)


class AddressIn(Schema):
    kind: str
    label: str = ''
    line1: str
    line2: str = ''
    city: str = ''
    state: str = ''
    state_code: str = ''
    pincode: str = ''
    is_default: bool = False


class AddressOut(AddressIn):
    id: int


def _address_out(address):
    return {f: getattr(address, f) for f in ('id', 'kind', 'label', 'line1', 'line2', 'city', 'state', 'state_code',
                                             'pincode', 'is_default')}


def _save_address(company, address, payload):
    data = payload.dict()
    if data['kind'] not in Address.Kind.values:
        raise HttpError(400, "Choose what the address is for.")
    if not data['line1'].strip():
        raise HttpError(400, "Enter the address.")
    if data['state_code'] and not (data['state_code'].isdigit() and len(data['state_code']) == 2):
        raise HttpError(400, "The GST state code is two digits, e.g. 33.")
    if data['pincode'] and not (data['pincode'].isdigit() and len(data['pincode']) == 6):
        raise HttpError(400, "A PIN code is six digits.")
    with transaction.atomic():
        for field, value in data.items():
            setattr(address, field, value.strip() if isinstance(value, str) else value)
        address.company = company
        address.save()
        if address.is_default:
            company.addresses.filter(kind=address.kind).exclude(pk=address.pk).update(is_default=False)
    return _address_out(address)


@router.get('/organization/addresses', response=List[AddressOut], tags=['organization'])
def list_addresses(request):
    return [_address_out(a) for a in membership_for(request).company.addresses.all()]


@router.post('/organization/addresses', response=AddressOut, tags=['organization'])
def create_address(request, payload: AddressIn):
    company = membership_for(request, 'org.manage', write=True).company
    return _save_address(company, Address(), payload)


@router.put('/organization/addresses/{address_id}', response=AddressOut, tags=['organization'])
def update_address(request, address_id: int, payload: AddressIn):
    company = membership_for(request, 'org.manage', write=True).company
    return _save_address(company, get_object_or_404(Address, pk=address_id, company=company), payload)


@router.delete('/organization/addresses/{address_id}', response={204: None}, tags=['organization'])
def delete_address(request, address_id: int):
    company = membership_for(request, 'org.manage', write=True).company
    get_object_or_404(Address, pk=address_id, company=company).delete()
    return 204, None


class BankAccountIn(Schema):
    account_name: str
    bank_name: str
    account_number: str
    ifsc: str
    branch: str = ''
    is_default: bool = False


class BankAccountOut(BankAccountIn):
    id: int


def _bank_out(account):
    return {f: getattr(account, f) for f in ('id', 'account_name', 'bank_name', 'account_number', 'ifsc', 'branch',
                                             'is_default')}


def _save_bank(company, account, payload):
    import re
    data = payload.dict()
    data['ifsc'] = data['ifsc'].strip().upper()
    data['account_number'] = data['account_number'].replace(' ', '')
    if not data['account_name'].strip() or not data['bank_name'].strip():
        raise HttpError(400, "Enter the account name and the bank.")
    if not re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}', data['ifsc']):
        raise HttpError(400, "An IFSC is 11 characters: four letters, a zero, then six letters or digits.")
    if not re.fullmatch(r'\d{9,18}', data['account_number']):
        raise HttpError(400, "An account number is 9 to 18 digits.")
    with transaction.atomic():
        for field, value in data.items():
            setattr(account, field, value.strip() if isinstance(value, str) else value)
        account.company = company
        account.save()
        if account.is_default:
            company.bank_accounts.exclude(pk=account.pk).update(is_default=False)
    return _bank_out(account)


@router.get('/organization/bank-accounts', response=List[BankAccountOut], tags=['organization'])
def list_bank_accounts(request):
    company = membership_for(request, 'org.manage').company
    return [_bank_out(a) for a in company.bank_accounts.all()]


@router.post('/organization/bank-accounts', response=BankAccountOut, tags=['organization'])
def create_bank_account(request, payload: BankAccountIn):
    company = membership_for(request, 'org.manage', write=True).company
    return _save_bank(company, BankAccount(), payload)


@router.put('/organization/bank-accounts/{account_id}', response=BankAccountOut, tags=['organization'])
def update_bank_account(request, account_id: int, payload: BankAccountIn):
    company = membership_for(request, 'org.manage', write=True).company
    return _save_bank(company, get_object_or_404(BankAccount, pk=account_id, company=company), payload)


@router.delete('/organization/bank-accounts/{account_id}', response={204: None}, tags=['organization'])
def delete_bank_account(request, account_id: int):
    company = membership_for(request, 'org.manage', write=True).company
    get_object_or_404(BankAccount, pk=account_id, company=company).delete()
    return 204, None


class PermissionOut(Schema):
    code: str
    label: str
    module: str
    side: Optional[str]
    light_allowed: bool
    included: bool
    needs_plan: Optional[str]
    side_enabled: bool


@router.get('/organization/permissions', response=List[PermissionOut], tags=['roles'])
def list_permissions(request):
    """Every permission, and whether this organization can use it now."""
    company = membership_for(request).company
    result = []
    for code, (label, module, feature, side) in catalog.PERMISSIONS.items():
        included = erp.includes(company.erp_plan, feature)
        needs = None if included else erp.first_plan_with(feature)
        result.append({
            'code': code, 'label': label, 'module': module, 'side': side,
            'light_allowed': code in catalog.LIGHT_PERMISSIONS, 'included': included,
            'needs_plan': erp.PLANS[needs]['label'] if needs else None,
            'side_enabled': side is None or (company.buying_enabled if side == catalog.BUYING else company.selling_enabled),
        })
    return result


# --- People ---------------------------------------------------------------------

class PersonRef(Schema):
    id: int
    name: str


class TeamRef(Schema):
    id: int
    name: str


class MemberOut(Schema):
    id: int
    user_id: int
    name: str
    email: str
    phone: str
    role: RoleRef
    teams: List[TeamRef]
    is_active: bool
    is_me: bool
    joined_at: datetime


class InvitationOut(Schema):
    id: int
    email: str
    name: str
    role: RoleRef
    teams: List[TeamRef]
    invited_by: str
    created_at: datetime
    expires_at: datetime
    expired: bool


class PeopleOut(Schema):
    members: List[MemberOut]
    invitations: List[InvitationOut]
    seats: SeatsOut
    can_manage: bool


class InviteIn(Schema):
    email: str
    first_name: str
    last_name: str = ''
    role_id: int
    team_ids: List[int] = []


class MemberIn(Schema):
    role_id: Optional[int] = None
    is_active: Optional[bool] = None
    team_ids: Optional[List[int]] = None


def _name(user):
    return user.get_full_name() or user.email


def _member_out(membership, me):
    teams = Team.objects.filter(company=membership.company, memberships__user=membership.user).order_by('name')
    user = membership.user
    return {
        'id': membership.pk, 'user_id': user.pk, 'name': _name(user), 'email': user.email, 'phone': user.phone,
        'role': _role_ref(membership.role), 'teams': [{'id': t.pk, 'name': t.name} for t in teams],
        'is_active': membership.is_active, 'is_me': user.pk == me.pk, 'joined_at': membership.created_at,
    }


def _invitation_out(invitation):
    from django.utils import timezone
    inviter = invitation.invited_by
    return {
        'id': invitation.pk, 'email': invitation.email,
        'name': f"{invitation.first_name} {invitation.last_name}".strip(),
        'role': _role_ref(invitation.role),
        'teams': [{'id': t.pk, 'name': t.name} for t in invitation.teams.all()],
        'invited_by': _name(inviter) if inviter else '', 'created_at': invitation.created_at,
        'expires_at': invitation.expires_at, 'expired': invitation.expires_at <= timezone.now(),
    }


def _teams(company, ids):
    teams = list(Team.objects.filter(company=company, pk__in=ids or []))
    if len(teams) != len(set(ids or [])):
        raise HttpError(400, "One of those teams isn't in this organization.")
    return teams


@router.get('/people', response=PeopleOut, tags=['people'])
def list_people(request):
    membership = membership_for(request)
    company = membership.company
    can_manage = bool(access.check(membership, 'users.manage'))
    members = Membership.objects.filter(company=company).select_related('user', 'role', 'company').order_by(
        '-is_active', 'user__first_name', 'user__email')
    invitations = Invitation.objects.filter(company=company, accepted_at__isnull=True, revoked_at__isnull=True) \
        .select_related('role', 'invited_by').prefetch_related('teams') if can_manage else []
    return {
        'members': [_member_out(m, request.user) for m in members],
        'invitations': [_invitation_out(i) for i in invitations],
        'seats': workspace.seat_usage(company),
        'can_manage': can_manage,
    }


@router.post('/people/invitations', response=InvitationOut, tags=['people'])
def create_invitation(request, payload: InviteIn):
    membership = membership_for(request, 'users.manage', write=True)
    company = membership.company
    role = get_object_or_404(Role, pk=payload.role_id, company=company)
    try:
        invitation = people.invite(company, request.user, email=payload.email, first_name=payload.first_name,
                                   last_name=payload.last_name, role=role, teams=_teams(company, payload.team_ids))
    except people.PeopleError as exc:
        _bad_request(exc)
    return _invitation_out(invitation)


@router.post('/people/invitations/{invitation_id}/resend', response=InvitationOut, tags=['people'])
def resend_invitation(request, invitation_id: int):
    company = membership_for(request, 'users.manage', write=True).company
    invitation = get_object_or_404(Invitation, pk=invitation_id, company=company)
    try:
        people.resend(invitation, request.user)
    except people.PeopleError as exc:
        _bad_request(exc)
    return _invitation_out(invitation)


@router.delete('/people/invitations/{invitation_id}', response={204: None}, tags=['people'])
def revoke_invitation(request, invitation_id: int):
    company = membership_for(request, 'users.manage', write=True).company
    try:
        people.revoke(get_object_or_404(Invitation, pk=invitation_id, company=company), request.user)
    except people.PeopleError as exc:
        _bad_request(exc)
    return 204, None


@router.patch('/people/members/{member_id}', response=MemberOut, tags=['people'])
def update_member(request, member_id: int, payload: MemberIn):
    company = membership_for(request, 'users.manage', write=True).company
    target = get_object_or_404(Membership.objects.select_related('user', 'role', 'company'), pk=member_id, company=company)
    changes = payload.dict(exclude_unset=True)
    role = get_object_or_404(Role, pk=changes['role_id'], company=company) if changes.get('role_id') else None
    teams = _teams(company, changes['team_ids']) if changes.get('team_ids') is not None else None
    try:
        target = people.change_member(target, request.user, role=role, is_active=changes.get('is_active'), teams=teams)
    except people.PeopleError as exc:
        _bad_request(exc)
    return _member_out(target, request.user)


# --- Roles ------------------------------------------------------------------------

class RoleOut(Schema):
    id: int
    key: str
    name: str
    description: str
    user_type: str
    is_system: bool
    locked: bool
    member_count: int
    permissions: List[str]


class RoleIn(Schema):
    name: str
    description: str = ''
    user_type: str = catalog.FULL
    permissions: List[str] = []


class RoleUpdateIn(Schema):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None


def _role_out(role):
    return {
        'id': role.pk, 'key': role.key, 'name': role.name, 'description': role.description,
        'user_type': role.user_type, 'is_system': role.is_system, 'locked': role.key == catalog.ORG_ADMIN,
        'member_count': role.memberships.filter(is_active=True).count(),
        'permissions': sorted(role.permissions.values_list('code', flat=True)),
    }


def _permission_rows(codes):
    codes = set(codes or [])
    unknown = codes - set(catalog.PERMISSIONS)
    if unknown:
        raise HttpError(400, f"Unknown permission: {', '.join(sorted(unknown))}.")
    return list(Permission.objects.filter(code__in=codes))


def _role_name(name):
    name = ' '.join((name or '').split())
    if not name:
        raise HttpError(400, "Give the role a name.")
    return name[:60]


@router.get('/roles', response=List[RoleOut], tags=['roles'])
def list_roles(request):
    company = membership_for(request).company
    return [_role_out(r) for r in people.roles_for(company).order_by('user_type', 'name')]


@router.post('/roles', response=RoleOut, tags=['roles'])
def create_role(request, payload: RoleIn):
    company = membership_for(request, 'roles.manage', write=True).company
    if payload.user_type not in (catalog.FULL, catalog.LIGHT):
        raise HttpError(400, "Choose full or light user.")
    name = _role_name(payload.name)
    key = slugify(name)[:40] or 'role'
    if company.roles.filter(key=key).exists() or company.roles.filter(name__iexact=name).exists():
        raise HttpError(400, f"There's already a role called {name}.")
    rows = _permission_rows(payload.permissions)
    try:
        with transaction.atomic():
            role = Role.objects.create(company=company, key=key, name=name, description=payload.description.strip()[:200],
                                       user_type=payload.user_type)
            role.permissions.set(rows)
    except (ValidationError, IntegrityError) as exc:
        _bad_request(exc)
    return _role_out(role)


@router.patch('/roles/{role_id}', response=RoleOut, tags=['roles'])
def update_role(request, role_id: int, payload: RoleUpdateIn):
    company = membership_for(request, 'roles.manage', write=True).company
    role = get_object_or_404(Role, pk=role_id, company=company)
    if role.key == catalog.ORG_ADMIN:
        raise HttpError(400, "The Org Admin role always has every permission and can't be changed.")
    changes = payload.dict(exclude_unset=True)
    try:
        with transaction.atomic():
            if 'name' in changes:
                name = _role_name(changes['name'])
                if company.roles.filter(name__iexact=name).exclude(pk=role.pk).exists():
                    raise HttpError(400, f"There's already a role called {name}.")
                role.name = name
            if 'description' in changes:
                role.description = (changes['description'] or '').strip()[:200]
            role.save()
            if changes.get('permissions') is not None:
                role.permissions.set(_permission_rows(changes['permissions']))
    except (ValidationError, IntegrityError) as exc:
        _bad_request(exc)
    return _role_out(role)


@router.delete('/roles/{role_id}', response={204: None}, tags=['roles'])
def delete_role(request, role_id: int):
    company = membership_for(request, 'roles.manage', write=True).company
    role = get_object_or_404(Role, pk=role_id, company=company)
    if role.is_system:
        raise HttpError(400, "The starting roles can't be deleted. Change their permissions instead.")
    if role.memberships.exists() or role.invitations.filter(accepted_at__isnull=True, revoked_at__isnull=True).exists():
        raise HttpError(400, "Move everyone (and any open invitations) to another role first.")
    role.delete()
    return 204, None


# --- Teams ------------------------------------------------------------------------

class TeamOut(Schema):
    id: int
    name: str
    head: Optional[PersonRef]
    members: List[PersonRef]


class TeamIn(Schema):
    name: str
    head_id: Optional[int] = None
    member_ids: List[int] = []


def _team_out(team):
    members = team.members.order_by('first_name', 'email')
    return {
        'id': team.pk, 'name': team.name,
        'head': {'id': team.head.pk, 'name': _name(team.head)} if team.head else None,
        'members': [{'id': u.pk, 'name': _name(u)} for u in members],
    }


def _save_team(company, team, payload):
    from core.models import User
    from .models import TeamMembership

    name = ' '.join((payload.name or '').split())[:60]
    if not name:
        raise HttpError(400, "Give the team a name.")
    if company.teams.filter(name__iexact=name).exclude(pk=team.pk).exists():
        raise HttpError(400, f"There's already a team called {name}.")
    member_ids = set(payload.member_ids or [])
    if payload.head_id:
        member_ids.add(payload.head_id)
    users = list(User.objects.filter(pk__in=member_ids, memberships__company=company).distinct())
    if len(users) != len(member_ids):
        raise HttpError(400, "Everyone in a team must be in this organization.")
    try:
        with transaction.atomic():
            team.company = company
            team.name = name
            team.head = next((u for u in users if u.pk == payload.head_id), None)
            team.save()
            TeamMembership.objects.filter(team=team).exclude(user__in=users).delete()
            for user in users:
                TeamMembership.objects.get_or_create(team=team, user=user)
    except ValidationError as exc:
        _bad_request(exc)
    return _team_out(team)


@router.get('/teams', response=List[TeamOut], tags=['teams'])
def list_teams(request):
    company = membership_for(request).company
    return [_team_out(t) for t in company.teams.select_related('head').order_by('name')]


@router.post('/teams', response=TeamOut, tags=['teams'])
def create_team(request, payload: TeamIn):
    company = membership_for(request, 'users.manage', write=True).company
    return _save_team(company, Team(), payload)


@router.put('/teams/{team_id}', response=TeamOut, tags=['teams'])
def update_team(request, team_id: int, payload: TeamIn):
    company = membership_for(request, 'users.manage', write=True).company
    return _save_team(company, get_object_or_404(Team, pk=team_id, company=company), payload)


@router.delete('/teams/{team_id}', response={204: None}, tags=['teams'])
def delete_team(request, team_id: int):
    company = membership_for(request, 'users.manage', write=True).company
    get_object_or_404(Team, pk=team_id, company=company).delete()
    return 204, None


# --- Partners -----------------------------------------------------------------------

class PartnerCompanyOut(Schema):
    id: int
    name: str
    legal_name: str
    gstin: str
    city: str
    state: str
    on_network: bool


class PartnerOut(Schema):
    id: int
    relation: str
    status: str
    waiting_on: Optional[str]
    blocked_by_us: bool
    partner: PartnerCompanyOut
    payment_terms_days: Optional[int]
    credit_limit: Optional[str]
    vendor_code: str
    customer_code: str
    editable_terms: List[str]
    created_at: datetime
    activated_at: Optional[datetime]


class SearchResultOut(Schema):
    id: int
    name: str
    legal_name: str
    city: str
    state: str
    relationships: List[str]


class ConnectIn(Schema):
    relation: str
    company_id: Optional[int] = None
    gstin: Optional[str] = None
    email: Optional[str] = None


class ConnectOut(Schema):
    partner: PartnerOut
    invite_email_sent: bool


class TermsIn(Schema):
    payment_terms_days: Optional[int] = None
    credit_limit: Optional[str] = None
    vendor_code: Optional[str] = None
    customer_code: Optional[str] = None


def _partner_out(relationship, company):
    data = partners.describe(relationship, company)
    data['credit_limit'] = str(data['credit_limit']) if data['credit_limit'] is not None else None
    return data


@router.get('/partners', response=List[PartnerOut], tags=['partners'])
def list_partners(request):
    company = membership_for(request).company
    return [_partner_out(r, company) for r in partners.for_company(company)]


@router.get('/partners/search', response=List[SearchResultOut], tags=['partners'])
def search_partners(request, q: str = ''):
    company = membership_for(request, 'partners.manage').company
    results = []
    for other in partners.search(company, q):
        statuses = [f"{'supplier' if r.supplier_id == other.pk else 'customer'}:{r.status}"
                    for r in partners.relationship_between(company, other)]
        results.append({'id': other.pk, 'name': other.name or other.legal_name, 'legal_name': other.legal_name,
                        'city': other.city, 'state': other.state, 'relationships': statuses})
    return results


@router.post('/partners', response=ConnectOut, tags=['partners'])
def connect_partner(request, payload: ConnectIn):
    company = membership_for(request, 'partners.manage', write=True).company
    if bool(payload.company_id) == bool(payload.gstin):
        raise HttpError(400, "Choose a company from the search, or enter a GSTIN.")
    target = None
    if payload.company_id:
        from accounts.models import Company
        target = Company.objects.filter(pk=payload.company_id, memberships__is_active=True).distinct().first()
        if target is None:
            raise HttpError(404, "That company isn't on MakeSetu.")
    try:
        relationship, sent = partners.connect(company, request.user, payload.relation, target=target,
                                              gstin=payload.gstin or '', email=payload.email or '')
    except partners.PartnerError as exc:
        _bad_request(exc)
    return {'partner': _partner_out(relationship, company), 'invite_email_sent': sent}


def _relationship(company, relationship_id):
    return get_object_or_404(partners.for_company(company), pk=relationship_id)


def _partner_action(request, relationship_id, action):
    company = membership_for(request, 'partners.manage', write=True).company
    relationship = _relationship(company, relationship_id)
    try:
        result = action(relationship, company, request.user)
    except partners.PartnerError as exc:
        _bad_request(exc)
    return result, company


@router.post('/partners/{relationship_id}/accept', response=PartnerOut, tags=['partners'])
def accept_partner(request, relationship_id: int):
    relationship, company = _partner_action(request, relationship_id, partners.accept)
    return _partner_out(relationship, company)


@router.post('/partners/{relationship_id}/decline', response={204: None}, tags=['partners'])
def decline_partner(request, relationship_id: int):
    _partner_action(request, relationship_id, partners.decline)
    return 204, None


@router.post('/partners/{relationship_id}/block', response=PartnerOut, tags=['partners'])
def block_partner(request, relationship_id: int):
    relationship, company = _partner_action(request, relationship_id, partners.block)
    return _partner_out(relationship, company)


@router.post('/partners/{relationship_id}/unblock', response=PartnerOut, tags=['partners'])
def unblock_partner(request, relationship_id: int):
    relationship, company = _partner_action(request, relationship_id, partners.unblock)
    return _partner_out(relationship, company)


@router.patch('/partners/{relationship_id}', response=PartnerOut, tags=['partners'])
def update_partner_terms(request, relationship_id: int, payload: TermsIn):
    company = membership_for(request, 'partners.manage', write=True).company
    relationship = _relationship(company, relationship_id)
    changes = payload.dict(exclude_unset=True)
    if 'payment_terms_days' in changes and changes['payment_terms_days'] is not None \
            and not 0 <= changes['payment_terms_days'] <= 365:
        raise HttpError(400, "Payment terms are 0 to 365 days.")
    if 'credit_limit' in changes and changes['credit_limit'] not in (None, ''):
        try:
            changes['credit_limit'] = Decimal(str(changes['credit_limit']).replace(',', ''))
        except InvalidOperation:
            raise HttpError(400, "Enter the credit limit as an amount in rupees.")
        if changes['credit_limit'] < 0:
            raise HttpError(400, "The credit limit can't be negative.")
    elif 'credit_limit' in changes:
        changes['credit_limit'] = None
    for code_field in ('vendor_code', 'customer_code'):
        if code_field in changes:
            changes[code_field] = (changes[code_field] or '').strip()[:30]
    try:
        partners.update_terms(relationship, company, request.user, changes)
    except partners.PartnerError as exc:
        _bad_request(exc)
    relationship.refresh_from_db()
    return _partner_out(relationship, company)

