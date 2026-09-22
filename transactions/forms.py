from django import forms
from django.forms import formset_factory
from .models import (
    Supplier, 
    PurchaseBill, 
    PurchaseItem,
    PurchaseBillDetails, 
    SaleBill, 
    SaleItem,
    SaleBillDetails,
    Customer,
    Demand,
    Quote,
    DemandParts
)
from inventory.models import Stock


from django import forms
from .models import Demand


# form used to select a supplier
class SelectSupplierForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.filter(is_deleted=False)
        self.fields['supplier'].widget.attrs.update({'class': 'textinput form-control'})
    class Meta:
        model = PurchaseBill
        fields = ['supplier']

# form used to render a single stock item form
class PurchaseItemForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['stock'].queryset = Stock.objects.filter(is_deleted=False)
        self.fields['stock'].widget.attrs.update({'class': 'textinput form-control setprice stock', 'required': 'true'})
        self.fields['quantity'].widget.attrs.update({'class': 'textinput form-control setprice quantity', 'min': '0', 'required': 'true'})
        self.fields['perprice'].widget.attrs.update({'class': 'textinput form-control setprice price', 'min': '0', 'required': 'true'})
    class Meta:
        model = PurchaseItem
        fields = ['stock', 'quantity', 'perprice']

# formset used to render multiple 'PurchaseItemForm'
PurchaseItemFormset = formset_factory(PurchaseItemForm, extra=1)

# form used to accept the other details for purchase bill
class PurchaseDetailsForm(forms.ModelForm):
    class Meta:
        model = PurchaseBillDetails
        fields = ['eway','veh', 'destination', 'po', 'cgst', 'sgst', 'igst', 'cess', 'tcs', 'total']


# form used for supplier
class SupplierForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['phone'].widget.attrs.update({'class': 'textinput form-control', 'maxlength': '10', 'pattern' : '[0-9]{10}', 'title' : 'Numbers only'})
    class Meta:
        model = Supplier
        fields = ['phone', 'address']
        widgets = {
            'address' : forms.Textarea(
                attrs = {
                    'class' : 'textinput form-control',
                    'rows'  : '4'
                }
            )
        }


# form used to get customer details
class SaleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs.update({'class': 'textinput form-control', 'pattern' : '[a-zA-Z\s]{1,50}', 'title' : 'Alphabets and Spaces only', 'required': 'true'})
        self.fields['phone'].widget.attrs.update({'class': 'textinput form-control', 'maxlength': '10', 'pattern' : '[0-9]{10}', 'title' : 'Numbers only', 'required': 'true'})
        self.fields['email'].widget.attrs.update({'class': 'textinput form-control'})
    class Meta:
        model = SaleBill
        fields = ['name', 'phone', 'address', 'email']
        widgets = {
            'address' : forms.Textarea(
                attrs = {
                    'class' : 'textinput form-control',
                    'rows'  : '4'
                }
            )
        }

# form used to render a single stock item form
class SaleItemForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['stock'].queryset = Stock.objects.filter(is_deleted=False)
        self.fields['stock'].widget.attrs.update({'class': 'textinput form-control setprice stock', 'required': 'true'})
        self.fields['quantity'].widget.attrs.update({'class': 'textinput form-control setprice quantity', 'min': '0', 'required': 'true'})
        self.fields['perprice'].widget.attrs.update({'class': 'textinput form-control setprice price', 'min': '0', 'required': 'true'})
    class Meta:
        model = SaleItem
        fields = ['stock', 'quantity', 'perprice']

# formset used to render multiple 'SaleItemForm'
SaleItemFormset = formset_factory(SaleItemForm, extra=1)

# form used to accept the other details for sales bill
class SaleDetailsForm(forms.ModelForm):
    class Meta:
        model = SaleBillDetails
        fields = ['eway','veh', 'destination', 'po', 'cgst', 'sgst', 'igst', 'cess', 'tcs', 'total']

class SelectDemand(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(SelectDemand, self).__init__(*args, **kwargs)

        if 'initial' in kwargs and 'quote_currency' in kwargs['initial']:
            self.fields['quote_currency'].initial = kwargs['initial']['quote_currency']

        for field_name in self.fields:
            self.fields[field_name].widget.attrs.update({'class': 'form-control'})

        if self.instance and self.instance.is_deleted:
            self.fields['title'].widget.attrs.update({'disabled': 'disabled'})

    class Meta:
        model = Demand
        fields = [
            'user', 'title', 'nda_required', 'quote_currency', 'request_reason', 'parts',
            'end_date', 'industry', 'is_deleted', 'rfq_desc', 'file'
        ]
        widgets = {
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),  # Set the widget for date picking
            'nda_required': forms.Select(choices=[(True, 'Yes'), (False, 'No')]),  # Change to dropdown with Yes/No
            'is_deleted': forms.HiddenInput(),  # Hide is_deleted field
        }



class DemandPartsForm(forms.ModelForm):
    class Meta:
        model = DemandParts
        fields = ['part_name', 'Part_desc', 'technology', 'Material', 'file', 'quantity']
        widgets = {
            'part_name': forms.TextInput(attrs={'class': 'form-control'}),
            'Part_desc': forms.TextInput(attrs={'class': 'form-control'}),
            'technology': forms.Select(attrs={'class': 'form-control'}),
            'Material': forms.Select(attrs={'class': 'form-control'}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
        }



class SelectQuote(forms.ModelForm):
    class Meta:
        model = Quote
        fields = [
            'demand',
            'supplier',
            'quote_price',
            'note',
        ]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quote_price'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['note'].widget.attrs.update({'class': 'form-control'})