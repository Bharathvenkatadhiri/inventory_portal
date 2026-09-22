from django.urls import path
from . import views

urlpatterns = [
    path('webhook/', views.RazorpayWebhookView.as_view(), name='razorpay-webhook'),
]
