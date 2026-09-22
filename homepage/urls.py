from django.urls import path
from django.contrib.auth.decorators import login_not_required
from . import views

handler404 = 'homepage.views.custom_404_view'

urlpatterns = [
    # login_not_required must wrap the as_view() result, not decorate the
    # class itself — View.as_view() doesn't propagate class-level
    # attributes to the callable that LoginRequiredMiddleware inspects.
    path('', login_not_required(views.HomeView.as_view()), name='home'),
    path('about/', login_not_required(views.AboutView.as_view()), name='about'),
    path('pricing/', login_not_required(views.PricingView.as_view()), name='pricing'),
    path('contact/', login_not_required(views.ContactView.as_view()), name='contact'),
    path('privacy-policy/', login_not_required(views.PrivacyPolicyView.as_view()), name='privacy-policy'),
    path('terms-of-service/', login_not_required(views.TermsOfServiceView.as_view()), name='terms-of-service'),
]
