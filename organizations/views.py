from functools import lru_cache

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.forms import TeamJoinForm
from accounts.models import PendingRegistration

from . import people, workspace


@lru_cache(maxsize=1)
def _built_index(mtime):
    return (settings.FRONTEND_DIST / 'index.html').read_text(encoding='utf-8')


@ensure_csrf_cookie
def app_shell(request, path=''):
    """/app/…: the signed-in ERP app (frontend/apps/web). Every path under
    /app is the same page; the app's router shows the right screen. The
    CSRF cookie it sends with API calls is set here."""
    if not workspace.is_member(request.user):
        if request.user.role == 'member':
            return render(request, 'organizations/no_access.html', status=403)
        return redirect('home')
    index = settings.FRONTEND_DIST / 'index.html'
    if not index.exists():
        return render(request, 'organizations/app_not_built.html', status=503)
    return HttpResponse(_built_index(index.stat().st_mtime))


@login_not_required
def accept_invitation(request, token):
    """An invited person joins an organization. New to MakeSetu: they set
    their name and password (the emailed link proves the address, as the
    sign-up code does) and are signed in. Already a member somewhere else:
    they sign in with that account and accept."""
    from core.models import User

    invitation = people.find_invitation(token)
    if invitation is None or not invitation.is_open:
        return render(request, 'organizations/accept_invitation.html', {'invalid': True}, status=404)
    company_name = invitation.company.name or invitation.company.legal_name
    context = {'invitation': invitation, 'company_name': company_name}
    existing = User.objects.filter(email__iexact=invitation.email).first()

    if request.user.is_authenticated:
        if request.user.email.lower() != invitation.email.lower():
            context['wrong_account'] = request.user
            return render(request, 'organizations/accept_invitation.html', context)
        if request.method == 'POST':
            try:
                membership = people.accept(invitation, request.user)
            except people.PeopleError as exc:
                messages.error(request, str(exc))
                return redirect('home')
            workspace.switch(request, membership.company_id)
            messages.success(request, f"You've joined {company_name}.")
            return redirect('erp-app')
        context['confirm'] = True
        return render(request, 'organizations/accept_invitation.html', context)

    if existing is not None:
        context['sign_in_first'] = True
        context['marketplace_account'] = existing.role != 'member'
        return render(request, 'organizations/accept_invitation.html', context)

    if request.method == 'POST':
        form = TeamJoinForm(request.POST, email=invitation.email)
        if form.is_valid():
            from accounts.otp import _unique_username
            from core.password_history import record

            with transaction.atomic():
                user = User(
                    username=_unique_username(invitation.email), email=invitation.email,
                    first_name=form.cleaned_data['first_name'], last_name=form.cleaned_data['last_name'],
                    role='member', email_verified=True,
                )
                user.set_password(form.cleaned_data['password1'])
                user.save()
                record(user, user.password)
                membership = people.accept(invitation, user)
                PendingRegistration.objects.filter(email__iexact=invitation.email).delete()
            login(request, user, backend='core.backends.EmailBackend')
            request.session.set_expiry(0)  # like a sign-in without "Remember me"
            workspace.switch(request, membership.company_id)
            return redirect('erp-app')
    else:
        form = TeamJoinForm(initial={'first_name': invitation.first_name, 'last_name': invitation.last_name},
                            email=invitation.email)
    context['form'] = form
    return render(request, 'organizations/accept_invitation.html', context)
