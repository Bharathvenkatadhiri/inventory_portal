from django.urls import path
from django.contrib.auth.decorators import login_not_required
from . import team_views, views

urlpatterns = [
    path('team/', team_views.TeamView.as_view(), name='team'),
    path('team/invite/', team_views.TeamInviteView.as_view(), name='team-invite'),
    path('team/invitations/<int:pk>/revoke/', team_views.TeamInvitationRevokeView.as_view(), name='team-invitation-revoke'),
    path('team/members/<int:pk>/role/', team_views.TeamMemberRoleView.as_view(), name='team-member-role'),
    path('team/members/<int:pk>/active/', team_views.TeamMemberActiveView.as_view(), name='team-member-active'),
    path('team/members/<int:pk>/make-manager/', team_views.TeamTransferView.as_view(), name='team-transfer'),
    path('team/join/<str:token>/', team_views.team_join, name='team-join'),
    path('register', views.register, name='register'),
    path('verify-email', views.verify_email, name='verify-email'),
    # login_not_required must wrap the as_view() result, not decorate the
    # class itself — View.as_view() doesn't propagate class-level
    # attributes to the callable that LoginRequiredMiddleware inspects.
    path('register-supplier', login_not_required(views.CreateSupplier.as_view()), name='register-supplier'),
    path('register-customer', login_not_required(views.CreateCustomer.as_view()), name='register-customer'),
    path('profile', views.ViewProfileDetails, name='profile'),

    path('company/', views.CompanyProfileView.as_view(), name='company-profile'),
    path('company/about/', views.CompanyAboutUpdateView.as_view(), name='company-about-update'),
    path('company/contact/', views.CompanyContactUpdateView.as_view(), name='company-contact-update'),
    path('company/capacity/', views.CompanyCapacityUpdateView.as_view(), name='company-capacity-update'),
    path('company/lut/', views.CompanyLUTUpdateView.as_view(), name='company-lut-update'),
    path('company/capabilities/add/', views.CompanyCapabilityAddView.as_view(), name='company-capability-add'),
    path('company/capabilities/<int:pk>/remove/', views.CompanyCapabilityRemoveView.as_view(), name='company-capability-remove'),
    path('company/materials/add/', views.CompanyMaterialAddView.as_view(), name='company-material-add'),
    path('company/materials/<int:pk>/remove/', views.CompanyMaterialRemoveView.as_view(), name='company-material-remove'),
    path('company/machines/add/', views.CompanyMachineAddView.as_view(), name='company-machine-add'),
    path('company/machines/<int:pk>/remove/', views.CompanyMachineRemoveView.as_view(), name='company-machine-remove'),
    path('company/photos/upload/', views.CompanyPhotoUploadView.as_view(), name='company-photo-upload'),
    path('company/photos/<int:pk>/delete/', views.CompanyPhotoDeleteView.as_view(), name='company-photo-delete'),
    path('company/certifications/upload/', views.CompanyCertificationUploadView.as_view(), name='company-certification-upload'),
    path('company/certifications/<int:pk>/delete/', views.CompanyCertificationDeleteView.as_view(), name='company-certification-delete'),

    path('suppliers/', views.SupplierListView.as_view(), name='suppliers-list'),
    path('suppliers/directory/', views.SupplierDirectoryView.as_view(), name='supplier-directory'),
    path('suppliers/<pk>/edit', views.SupplierUpdateView.as_view(), name='edit-supplier'),
    path('suppliers/<pk>/delete', views.SupplierDeleteView.as_view(), name='delete-supplier'),
    path('suppliers/<pk>/activate', views.SupplieractivateView.as_view(), name='activate-supplier'),
    path('supplier/<pk>', views.SupplierView.as_view(), name='supplier'),

    path('customers/', views.CustomerListView.as_view(), name='customers-list'),
    path('customers/new', views.CustomerCreateView.as_view(), name='new-customer'),
    path('customers/<pk>/edit', views.CustomerUpdateView.as_view(), name='edit-customer'),
    path('customers/<pk>/delete', views.CustomerDeleteView.as_view(), name='delete-customer'),
    path('customers/<pk>/activate', views.CustomeractivateView.as_view(), name='activate-customer'),
    path('customers/<pk>', views.CustomerView.as_view(), name='customer'),

    path('subscription', views.SubscriptionView.as_view(), name='subscription-list'),
    path('subscription/<pk>/delete', views.SubscriptionDeleteView.as_view(), name='delete-subscription'),
    path('subscription/<pk>/edit', views.SubscriptionUpdateView.as_view(), name='edit-subscription'),
    path('subscription/upgrade/', views.SubscriptionUpgradeView.as_view(), name='subscription-upgrade'),

]