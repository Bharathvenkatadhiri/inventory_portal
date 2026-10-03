from django.urls import path

from . import views

urlpatterns = [
    path('verify/', views.verify_gstin, name='gst-verify'),
]
