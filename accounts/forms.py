from typing import Any
from django import forms
from django.contrib.auth.forms import UserCreationForm
#from django.contrib.auth.models import User
from django.conf import settings
from transactions.models import Customer
from django import forms
from .models import Supplier_details, SubscriptionPlan
from django.apps import apps
from core.settings import subscription_plan_details

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)

class UserRegistrationForm(UserCreationForm):
    is_staff = forms.ChoiceField(choices=[(1, 'Supplier'), (0, 'Buyer')], widget=forms.RadioSelect)
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'password1', 'password2', 'email','is_staff']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True
        self.fields['email'].required = True

class SupplierDetailsForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['companyname'].initial = 'Default Company Name'
        self.fields['phone'].widget.attrs.update({'readonly': 'readonly'})
        self.fields['address'].widget.attrs.update({'placeholder': 'Enter your address here'})
        if 'company_street' in self.fields:
            self.fields['company_street'].widget = forms.HiddenInput()
        if self.user:
            self.fields['user'].initial = self.user  # Assign user to the field

    class Meta:
        model = Supplier_details
        fields = [
            'user', 'companyname', 'email', 'phone', 'address', 'city', 'state', 'country',
            'activity_type', 'company_street', 'company_postalcode', 'company_city',
            'company_url', 'production_area', 'manufacturing_competency1', 'manufacturing_competency2',
            'info_source', 'amount_of_employees', 'turnover_per_year', 'certificates'
        ]
        widgets = {
            'user': forms.HiddenInput(),  # Hide the user field if you set it programmatically
            'activity_type': forms.Select(choices=Supplier_details.ACTIVITY_TYPE_CHOICES),
            'manufacturing_competency1': forms.Select(choices=Supplier_details.MANUFACTURING_COMPETENCY_CHOICES),
            'manufacturing_competency2': forms.Select(choices=Supplier_details.MANUFACTURING_COMPETENCY_CHOICES),
            'info_source': forms.Select(choices=Supplier_details.INFO_SOURCE_CHOICES),
            'amount_of_employees': forms.Select(choices=Supplier_details.EMPLOYEES_CHOICES),
            'turnover_per_year': forms.Select(choices=Supplier_details.TURNOVER_CHOICES),
            'certificates': forms.Select(choices=Supplier_details.CERTIFICATES_CHOICES),
        }

class updateSupplierDetailsForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['companyname'].initial = 'Default Company Name'
        self.fields['phone'].widget.attrs.update({'readonly': 'readonly'})
        self.fields['address'].widget.attrs.update({'placeholder': 'Enter your address here'})

    class Meta:
        model = Supplier_details
        fields = [
            'companyname', 'phone', 'address', 'city', 'state', 'country','activity_type',
            'company_street', 'company_postalcode', 'company_city',
            'company_url', 'production_area', 'manufacturing_competency1', 'manufacturing_competency2',
            'info_source', 'amount_of_employees', 'turnover_per_year', 'certificates'
        ]
        widgets = {
            'activity_type': forms.Select(choices=Supplier_details.ACTIVITY_TYPE_CHOICES),
            'manufacturing_competency1': forms.Select(choices=Supplier_details.MANUFACTURING_COMPETENCY_CHOICES),
            'manufacturing_competency2': forms.Select(choices=Supplier_details.MANUFACTURING_COMPETENCY_CHOICES),
            'info_source': forms.Select(choices=Supplier_details.INFO_SOURCE_CHOICES),
            'amount_of_employees': forms.Select(choices=Supplier_details.EMPLOYEES_CHOICES),
            'turnover_per_year': forms.Select(choices=Supplier_details.TURNOVER_CHOICES),
            'certificates': forms.Select(choices=Supplier_details.CERTIFICATES_CHOICES),
        }        

class SelectCustomer(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = User.objects.filter(customer__isnull=True)
        self.fields['user'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['Name'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['type_of_business'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['Address'].widget.attrs.update({'class': 'form-control'})
        self.fields['phone'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['email'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['EORI_number'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['VAT_number'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['is_deleted'].widget.attrs.update({'class': 'form-check-input'})

    class Meta:
        model = Customer
        fields = ['Name','type_of_business','Address','phone','email','EORI_number','VAT_number','is_deleted','user']

class updateCustomer(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['Name'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['type_of_business'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['Address'].widget.attrs.update({'class': 'form-control'})
        self.fields['phone'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['email'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['EORI_number'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['VAT_number'].widget.attrs.update({'class': 'form-control', 'required': 'true'})

    class Meta:
        model = Customer
        fields = ['Name','type_of_business','Address','phone','email','EORI_number','VAT_number']


class UpdateSubscription(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plan_type'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['end_date'].widget.attrs.update({'class': 'form-control'})
        self.fields['is_active'].widget.attrs.update({'class': 'form-check-input'})

    class Meta:
        model = SubscriptionPlan
        fields = ['plan_type','end_date','is_active']
        widgets = {
            'end_date': forms.DateInput(attrs={'type': 'date'})  # HTML5 date picker
        }
        
    def clean(self):
        cleaned_data = super().clean()
        plan_type = cleaned_data.get('plan_type')
        
        # Set the value of dependent_field based on the selection
        if plan_type == 'basic':
            cleaned_data['price'] = subscription_plan_details[plan_type]['price']
            cleaned_data['rfq_limit'] = subscription_plan_details[plan_type]['rfq_limit']
        elif plan_type == 'standard':
            cleaned_data['price'] = subscription_plan_details[plan_type]['price']
            cleaned_data['rfq_limit'] = subscription_plan_details[plan_type]['rfq_limit']
        else:
            cleaned_data['price'] = subscription_plan_details[plan_type]['price']
            cleaned_data['rfq_limit'] = subscription_plan_details[plan_type]['rfq_limit']
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        # Ensure the dependent_field is set correctly
        if self.cleaned_data['plan_type'] == 'basic':
            instance.price = subscription_plan_details[self.cleaned_data['plan_type']]['price']
            instance.rfq_limit = subscription_plan_details[self.cleaned_data['plan_type']]['rfq_limit']
        elif self.cleaned_data['plan_type'] == 'standard':
            instance.price = subscription_plan_details[self.cleaned_data['plan_type']]['price']
            instance.rfq_limit = subscription_plan_details[self.cleaned_data['plan_type']]['rfq_limit']
        else:
            instance.price = subscription_plan_details[self.cleaned_data['plan_type']]['price']
            instance.rfq_limit = subscription_plan_details[self.cleaned_data['plan_type']]['rfq_limit']
        
        if commit:
            instance.save()
        return instance    
