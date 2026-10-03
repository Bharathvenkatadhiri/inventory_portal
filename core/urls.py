
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls import handler404
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from homepage.views import CustomLoginView, CustomLogoutView, ReauthView, ThrottledPasswordResetView

handler404 = 'homepage.views.custom_404_view'


def _not_public(request, path):
    raise Http404


@login_not_required
def _admin_login(request):
    # Django admin's own login form has none of core/login_throttle's
    # brute-force lockout, so staff accounts — the most valuable ones —
    # could be password-guessed there without limit. Send it through the
    # throttled login page instead, coming back to the admin afterwards.
    next_url = request.GET.get('next') or reverse('admin:index')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse('admin:index')
    return redirect(f"{reverse('login')}?{urlencode({'next': next_url})}")


urlpatterns = [
    path('admin/login/', _admin_login),
    path('admin/', admin.site.urls, name='admin'),
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('reauth/', ReauthView.as_view(), name='reauth'),

    path('password-reset/', ThrottledPasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html',
    ), name='password_reset_complete'),

    path('api/gst/', include('gst.urls')),

    path('', include('homepage.urls')),
    path('accounts/', include('accounts.urls')),
    path('billing/', include('billing.urls')),
    path('marketplace/', include('marketplace.urls')),
]

if settings.DEBUG:
    # Message attachments are deliberately not served here: they must go
    # through marketplace's MessageAttachmentDownloadView, which checks the
    # viewer is part of the conversation.
    urlpatterns += [
        path(f"{settings.MEDIA_URL.lstrip('/')}message_attachments/<path:path>", _not_public),
    ] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)