from django.urls import path
from django.conf.urls import url,handler404
from . import views

handler404 = 'homepage.views.custom_404_view'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('about/', views.AboutView.as_view(), name='about')
]