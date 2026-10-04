from django.urls import path

from . import views

urlpatterns = [
    path('checkout/', views.CheckoutView.as_view(), name='billing-checkout'),
    path('change/', views.ChangePlanView.as_view(), name='billing-change'),
    path('payments/<int:pk>/return/', views.PaymentReturnView.as_view(), name='billing-return'),
    path('cancel/', views.CancelView.as_view(), name='billing-cancel'),
    path('reactivate/', views.ReactivateView.as_view(), name='billing-reactivate'),
    path('scheduled-change/cancel/', views.CancelScheduledChangeView.as_view(), name='billing-cancel-change'),
    path('intended-plan/dismiss/', views.DismissIntendedPlanView.as_view(), name='billing-dismiss-intended'),
    path('renewal-alert/dismiss/', views.DismissRenewalAlertView.as_view(), name='billing-renewal-alert-dismiss'),
    path('webhook/<str:gateway>/', views.WebhookView.as_view(), name='billing-webhook'),
    path('mock/checkout/<str:order_id>/', views.MockCheckoutView.as_view(), name='billing-mock-checkout'),
]
