import django_filters
from .models import Customer
class CustomerFilter(django_filters.FilterSet):
    email = django_filters.CharFilter(lookup_expr='icontains')
    class Meta:
        model = Customer
        fields = ['email']

