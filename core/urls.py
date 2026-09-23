
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls import handler404
from homepage.views import CustomLoginView, CustomLogoutView
from accounts.views import verify_gstin_view

handler404 = 'homepage.views.custom_404_view'

urlpatterns = [
    path('admin/', admin.site.urls, name='admin'),
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),

    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='registration/password_reset_form.html',
        email_template_name='registration/password_reset_email.html',
        subject_template_name='registration/password_reset_subject.txt',
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html',
    ), name='password_reset_complete'),

    path('api/companies/verify-gstin/', verify_gstin_view, name='verify-gstin'),

    path('', include('homepage.urls')),
    path('accounts/', include('accounts.urls')),
    path('marketplace/', include('marketplace.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)