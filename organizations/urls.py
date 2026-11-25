from django.urls import path, re_path

from . import views

urlpatterns = [
    path('app/', views.app_shell, name='erp-app'),
    re_path(r'^app/(?P<path>.+)$', views.app_shell, name='erp-app-path'),
    path('accounts/invitations/<str:token>/', views.accept_invitation, name='org-invitation-accept'),
]
