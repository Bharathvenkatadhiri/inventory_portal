from django.urls import path
from django.contrib.auth.decorators import login_not_required
from . import views

urlpatterns = [
    path('register', views.register, name='register'),
    # login_not_required must wrap the as_view() result, not decorate the
    # class itself — View.as_view() doesn't propagate class-level
    # attributes to the callable that LoginRequiredMiddleware inspects.
    path('register-supplier', login_not_required(views.CreateSupplier.as_view()), name='register-supplier'),
    path('register-customer', login_not_required(views.CreateCustomer.as_view()), name='register-customer'),
    path('profile', views.ViewProfileDetails, name='profile'),

    path('suppliers/', views.SupplierListView.as_view(), name='suppliers-list'),
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

]