from typing import Any
from django import forms
from django.contrib.auth.forms import UserCreationForm
#from django.contrib.auth.models import User
from django.conf import settings
from django import forms
from .models import (
    ManufacturerProfile, ConsumerProfile, SubscriptionPlan,
    Machine, Certification, ManufacturingTech, MaterialCapability,
)
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

    def save(self, commit=True):
        # `is_staff` here doubles as the Supplier/Buyer choice (see the field
        # above) but `role` was never being set from it, so every signup
        # stayed at the model's "consumer" default regardless of the choice
        # made on this form.
        user = super().save(commit=False)
        user.role = 'manufacturer' if str(self.cleaned_data.get('is_staff')) == '1' else 'consumer'
        if commit:
            user.save()
        return user

class SupplierDetailsForm(forms.ModelForm):
    """
    Legal/company name, address, city, state and country are intentionally
    NOT form fields here: they are populated server-side from the verified
    Company record (see accounts.views.CreateSupplier.form_valid), not from
    posted data, so a client can't submit an unverified company identity.
    """
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if 'company_street' in self.fields:
            self.fields['company_street'].widget = forms.HiddenInput()
        if self.user:
            self.fields['user'].initial = self.user  # Assign user to the field

    class Meta:
        model = ManufacturerProfile
        fields = [
            'user', 'email', 'phone',
            'activity_type', 'company_street', 'company_postalcode', 'company_city',
            'production_area', 'manufacturing_competency1', 'manufacturing_competency2',
            'info_source', 'amount_of_employees', 'turnover_per_year', 'certificates'
        ]
        widgets = {
            'user': forms.HiddenInput(),  # Hide the user field if you set it programmatically
            'activity_type': forms.Select(choices=ManufacturerProfile.ACTIVITY_TYPE_CHOICES),
            'manufacturing_competency1': forms.Select(choices=ManufacturerProfile.MANUFACTURING_COMPETENCY_CHOICES),
            'manufacturing_competency2': forms.Select(choices=ManufacturerProfile.MANUFACTURING_COMPETENCY_CHOICES),
            'info_source': forms.Select(choices=ManufacturerProfile.INFO_SOURCE_CHOICES),
            'amount_of_employees': forms.Select(choices=ManufacturerProfile.EMPLOYEES_CHOICES),
            'turnover_per_year': forms.Select(choices=ManufacturerProfile.TURNOVER_CHOICES),
            'certificates': forms.Select(choices=ManufacturerProfile.CERTIFICATES_CHOICES),
        }

class updateSupplierDetailsForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['companyname'].initial = 'Default Company Name'
        self.fields['phone'].widget.attrs.update({'readonly': 'readonly'})
        self.fields['address'].widget.attrs.update({'placeholder': 'Enter your address here'})

    class Meta:
        model = ManufacturerProfile
        fields = [
            'companyname', 'phone', 'address', 'city', 'state', 'country','activity_type',
            'company_street', 'company_postalcode', 'company_city',
            'company_url', 'production_area', 'manufacturing_competency1', 'manufacturing_competency2',
            'info_source', 'amount_of_employees', 'turnover_per_year', 'certificates'
        ]
        widgets = {
            'activity_type': forms.Select(choices=ManufacturerProfile.ACTIVITY_TYPE_CHOICES),
            'manufacturing_competency1': forms.Select(choices=ManufacturerProfile.MANUFACTURING_COMPETENCY_CHOICES),
            'manufacturing_competency2': forms.Select(choices=ManufacturerProfile.MANUFACTURING_COMPETENCY_CHOICES),
            'info_source': forms.Select(choices=ManufacturerProfile.INFO_SOURCE_CHOICES),
            'amount_of_employees': forms.Select(choices=ManufacturerProfile.EMPLOYEES_CHOICES),
            'turnover_per_year': forms.Select(choices=ManufacturerProfile.TURNOVER_CHOICES),
            'certificates': forms.Select(choices=ManufacturerProfile.CERTIFICATES_CHOICES),
        }        

class CompanyAboutForm(forms.ModelForm):
    class Meta:
        model = ManufacturerProfile
        fields = ['about', 'cover_image']
        widgets = {
            'about': forms.Textarea(attrs={'class': 'field-input', 'rows': 4}),
            'cover_image': forms.ClearableFileInput(attrs={'class': 'field-input'}),
        }


class CompanyContactForm(forms.ModelForm):
    class Meta:
        model = ManufacturerProfile
        fields = ['contact_name', 'contact_role', 'contact_phone']
        widgets = {
            'contact_name': forms.TextInput(attrs={'class': 'field-input'}),
            'contact_role': forms.TextInput(attrs={'class': 'field-input'}),
            'contact_phone': forms.TextInput(attrs={'class': 'field-input'}),
        }


class CompanyCapacityForm(forms.ModelForm):
    class Meta:
        model = ManufacturerProfile
        fields = ['minimum_order_qty', 'typical_lead_time_days', 'accepting_rfqs']
        widgets = {
            'minimum_order_qty': forms.NumberInput(attrs={'class': 'field-input'}),
            'typical_lead_time_days': forms.NumberInput(attrs={'class': 'field-input'}),
        }


class MachineForm(forms.ModelForm):
    class Meta:
        model = Machine
        fields = ['machine_type', 'make_model', 'quantity', 'work_envelope']
        widgets = {
            'machine_type': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. 5-axis VMC'}),
            'make_model': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. DMG Mori DMU 50'}),
            'quantity': forms.NumberInput(attrs={'class': 'field-input'}),
            'work_envelope': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. 650 x 520 x 475 mm'}),
        }


class CertificationForm(forms.ModelForm):
    class Meta:
        model = Certification
        fields = ['name', 'valid_until', 'document']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. ISO 9001:2015'}),
            'valid_until': forms.DateInput(attrs={'type': 'date', 'class': 'field-input'}),
            'document': forms.ClearableFileInput(attrs={'class': 'field-input'}),
        }


class CapabilityAddForm(forms.Form):
    capability = forms.ModelChoiceField(
        queryset=ManufacturingTech.objects.all(),
        widget=forms.Select(attrs={'class': 'field-input'}),
    )


class MaterialAddForm(forms.Form):
    material = forms.ModelChoiceField(
        queryset=MaterialCapability.objects.all(),
        widget=forms.Select(attrs={'class': 'field-input'}),
    )


class SelectCustomer(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = User.objects.filter(consumerprofile__isnull=True)
        self.fields['user'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['Name'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['type_of_business'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['Address'].widget.attrs.update({'class': 'form-control'})
        self.fields['phone'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['email'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['EORI_number'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['VAT_number'].widget.attrs.update({'class': 'form-control', 'required': 'true'})

    class Meta:
        model = ConsumerProfile
        # is_deleted intentionally excluded: this form is used for creating a
        # customer (self-registration and admin "New Customer"), where a new
        # record should always start active. Deactivating/reactivating an
        # existing one goes through the dedicated activate/deactivate views.
        fields = ['Name','type_of_business','Address','phone','email','EORI_number','VAT_number','user']

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
        model = ConsumerProfile
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
