"""Settings > Team: the company's members, invitations and activity log,
and the public page where an invited person joins. Who may do what is
decided in accounts.team."""
import logging
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from core import session_security
from core.emails import send_template_email
from . import team
from .forms import TeamInviteForm, TeamJoinForm
from .models import PendingRegistration, SubscriptionPlan, TeamActivity, TeamInvitation, TeamMember

User = get_user_model()
logger = logging.getLogger(__name__)


def _team_url():
    return reverse('team')


def _company_or_404(user):
    profile = team.company(user)
    if profile is None:
        raise Http404
    return profile


def _invitable_roles(user):
    return [role for role in (TeamMember.SUPERVISOR, TeamMember.MEMBER) if team.can_invite(user, role)]


def _name(user):
    return user.get_full_name() or user.email


def _company_name(profile):
    return getattr(profile, 'companyname', None) or getattr(profile, 'Name', None) or str(profile)


class TeamView(LoginRequiredMixin, View):
    def get(self, request):
        profile = team.company(request.user)
        if profile is None:
            messages.info(request, "Your company profile isn't set up yet.")
            return redirect(reverse('home'))
        return render(request, 'team/team.html', self._context(request, profile, TeamInviteForm(roles=_invitable_roles(request.user))))

    @staticmethod
    def _context(request, profile, invite_form):
        user = request.user
        members = list(team.members_of(profile).order_by('-user__is_active', 'role', 'user__first_name'))
        for member in members:
            member.can_manage = team.can_manage_member(user, member)
        invitations = list(team.open_invitations_of(profile).select_related('invited_by'))
        for invitation in invitations:
            invitation.can_manage = team.can_manage_member(user, invitation)
        tab = request.GET.get('tab', 'members')
        can_see_activity = team.can_approve(user)
        return {
            'profile': profile,
            'company_name': _company_name(profile),
            'manager': profile.user,
            'members': members,
            'invitations': invitations,
            'invite_form': invite_form,
            'can_invite': bool(_invitable_roles(user)),
            'is_manager': team.is_manager(user),
            'my_role': team.role_label(user),
            'seats_used': team.seats_used(profile),
            'seat_limit': team.seat_limit(profile),
            'has_free_seat': team.has_free_seat(profile),
            'tab': tab if (tab != 'activity' or can_see_activity) else 'members',
            'can_see_activity': can_see_activity,
            'activity': TeamActivity.objects.filter(**team.company_filter(profile))[:200] if can_see_activity else [],
        }


class TeamInviteView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request):
        profile = _company_or_404(request.user)
        roles = _invitable_roles(request.user)
        if not roles:
            raise Http404
        form = TeamInviteForm(request.POST, roles=roles)
        if form.is_valid():
            email = form.cleaned_data['email']
            if team.open_invitations_of(profile).filter(email__iexact=email).exists():
                form.add_error('email', "There's already an open invitation for this email. Revoke it first to send a new one.")
            elif not team.has_free_seat(profile):
                form.add_error(None, "Your plan's team is full. Upgrade the plan, or deactivate someone or revoke an invitation first.")
        if not form.is_valid():
            context = TeamView._context(request, profile, form)
            context['show_invite_form'] = True
            return render(request, 'team/team.html', context, status=400)

        raw_token, token_hash = team.new_invitation_token()
        invitation = TeamInvitation.objects.create(
            **team.company_filter(profile), email=form.cleaned_data['email'],
            first_name=form.cleaned_data['first_name'], last_name=form.cleaned_data['last_name'],
            role=form.cleaned_data['role'], token_hash=token_hash, invited_by=request.user,
            expires_at=timezone.now() + team.INVITATION_TTL,
        )
        send_template_email(
            invitation.email, 'emails/team_invitation_subject.txt', 'emails/team_invitation.txt',
            {
                'first_name': invitation.first_name, 'inviter_name': _name(request.user),
                'company_name': _company_name(profile), 'role_label': invitation.get_role_display(),
                'ttl_days': team.INVITATION_TTL.days,
                'url': f"{settings.SITE_URL}{reverse('team-join', kwargs={'token': raw_token})}",
            },
            html_template='emails/team_invitation.html',
        )
        team.log(request.user, 'team.invited', f"Invited {invitation.email} as {invitation.get_role_display()}", _team_url(), profile)
        messages.success(request, f"Invitation sent to {invitation.email}.")
        return redirect(_team_url())


class TeamInvitationRevokeView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        invitation = get_object_or_404(TeamInvitation, pk=pk)
        if not team.can_manage_member(request.user, invitation):
            raise Http404
        if invitation.is_open:
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=['revoked_at'])
            team.log(request.user, 'team.invite_revoked', f"Revoked the invitation for {invitation.email}", _team_url())
            messages.success(request, f"Invitation for {invitation.email} revoked.")
        return redirect(_team_url())


class TeamMemberRoleView(LoginRequiredMixin, View):
    """The manager switches someone between Supervisor and User."""
    http_method_names = ['post']

    def post(self, request, pk):
        member = get_object_or_404(TeamMember.objects.select_related('user'), pk=pk)
        role = request.POST.get('role')
        if not (team.is_manager(request.user) and team.belongs_to(member, team.company(request.user))):
            raise Http404
        if role not in dict(TeamMember.ROLE_CHOICES):
            messages.error(request, "Unknown role.")
        elif role != member.role:
            member.role = role
            member.save(update_fields=['role'])
            team.log(request.user, 'team.role_changed', f"Made {_name(member.user)} a {member.get_role_display()}", _team_url())
            messages.success(request, f"{_name(member.user)} is now a {member.get_role_display()}.")
        return redirect(_team_url())


class TeamMemberActiveView(LoginRequiredMixin, View):
    """Deactivating stops someone signing in (User.is_active); what they
    created stays with the company. Reactivating needs a free seat."""
    http_method_names = ['post']

    def post(self, request, pk):
        member = get_object_or_404(TeamMember.objects.select_related('user'), pk=pk)
        if not team.can_manage_member(request.user, member):
            raise Http404
        profile = team.company(request.user)
        activate = request.POST.get('action') == 'activate'
        if activate and not member.user.is_active:
            if not team.has_free_seat(profile):
                messages.error(request, "Your plan's team is full. Upgrade the plan or free a seat first.")
                return redirect(_team_url())
            member.user.is_active = True
            member.user.save(update_fields=['is_active'])
            team.log(request.user, 'team.reactivated', f"Reactivated {_name(member.user)}", _team_url())
            messages.success(request, f"{_name(member.user)} can sign in again.")
        elif not activate and member.user.is_active:
            member.user.is_active = False
            member.user.save(update_fields=['is_active'])
            team.log(request.user, 'team.deactivated', f"Deactivated {_name(member.user)}", _team_url())
            messages.success(request, f"{_name(member.user)} has been deactivated and can no longer sign in.")
        return redirect(_team_url())


class TeamTransferView(LoginRequiredMixin, View):
    """The manager hands the company account to an active supervisor, who
    becomes the manager (and holder of the plan); the old manager stays on
    as a supervisor."""
    http_method_names = ['post']

    def post(self, request, pk):
        member = get_object_or_404(TeamMember.objects.select_related('user'), pk=pk)
        profile = team.company(request.user)
        if not (team.is_manager(request.user) and team.belongs_to(member, profile)):
            raise Http404
        if member.role != TeamMember.SUPERVISOR or not member.user.is_active:
            messages.error(request, "Only an active supervisor can become the manager. Make them a supervisor first.")
            return redirect(_team_url())
        reauth = session_security.require_recent_auth(request, _team_url())
        if reauth is not None:
            return reauth
        old_manager, new_manager = request.user, member.user
        with transaction.atomic():
            company_kind = team.company_kind(old_manager)
            member.delete()
            profile.user = new_manager
            profile.save(update_fields=['user'])
            TeamMember.objects.create(
                user=old_manager, role=TeamMember.SUPERVISOR, invited_by=new_manager,
                **({'buyer': profile} if company_kind == 'buyer' else {'supplier': profile}),
            )
            # SubscriptionPlan.user_profile points at the user's email.
            SubscriptionPlan.objects.filter(user_profile=old_manager).update(user_profile=new_manager)
        team.forget(old_manager)
        team.log(old_manager, 'team.manager_transferred', f"Made {_name(new_manager)} the manager", _team_url(), profile)
        messages.success(request, f"{_name(new_manager)} is now the manager. You're a supervisor.")
        return redirect(_team_url())


def _username_for(email):
    base = re.sub(r'[^\w.@+-]', '', email.split('@')[0])[:140] or 'member'
    candidate, n = base, 1
    while User.objects.filter(username=candidate).exists():
        n += 1
        candidate = f"{base}{n}"
    return candidate


@login_not_required
def team_join(request, token):
    """An invited person sets their name and password and joins the company.
    Following the emailed link proves they own the address, the same as
    the registration OTP does."""
    invitation = TeamInvitation.objects.select_related('buyer', 'supplier', 'invited_by').filter(token_hash=team.hash_token(token)).first()
    if invitation is None or not invitation.is_open:
        return render(request, 'team/join.html', {'invalid': True}, status=404)
    profile = invitation.buyer or invitation.supplier
    context = {'invitation': invitation, 'company_name': _company_name(profile)}
    if request.user.is_authenticated:
        context['signed_in_as'] = request.user
        return render(request, 'team/join.html', context)

    if request.method == 'POST':
        form = TeamJoinForm(request.POST, email=invitation.email)
        if User.objects.filter(email__iexact=invitation.email).exists():
            form.add_error(None, "This email already has a MakeSetu account. Ask your manager to invite a different address.")
        if form.is_valid():
            with transaction.atomic():
                user = User(
                    username=_username_for(invitation.email), email=invitation.email,
                    first_name=form.cleaned_data['first_name'], last_name=form.cleaned_data['last_name'],
                    role='consumer' if invitation.buyer_id else 'manufacturer', email_verified=True,
                )
                user.set_password(form.cleaned_data['password1'])
                user.save()
                TeamMember.objects.create(
                    user=user, buyer=invitation.buyer, supplier=invitation.supplier,
                    role=invitation.role, invited_by=invitation.invited_by,
                )
                invitation.accepted_at = timezone.now()
                invitation.save(update_fields=['accepted_at'])
                # A half-finished self-registration under the same address would
                # otherwise fail later on the now-taken email.
                PendingRegistration.objects.filter(email__iexact=invitation.email).delete()
            team.log(user, 'team.joined', f"{_name(user)} joined as {invitation.get_role_display()}", '', profile)
            messages.success(request, f"Welcome to {context['company_name']}! Sign in with {user.email} and your new password.")
            return redirect(reverse('login'))
    else:
        form = TeamJoinForm(initial={'first_name': invitation.first_name, 'last_name': invitation.last_name}, email=invitation.email)
    context['form'] = form
    return render(request, 'team/join.html', context)
