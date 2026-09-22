from django.urls import path
from . import views

urlpatterns = [
    path('register', views.register, name='register'),
    path('register-supplier', views.CreateSupplier.as_view(), name='register-supplier'),
    path('register-customer', views.CreateCustomer.as_view(), name='register-customer'),
    path('profile', views.ViewProfileDetails, name='profile'),

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