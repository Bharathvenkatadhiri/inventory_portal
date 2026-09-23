from django import forms
from django.forms import formset_factory, inlineformset_factory

from .models import Requirement, RequirementPart, Quote, Order, ProductionUpdate, RequirementQuestion


class SelectRequirement(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'initial' in kwargs and 'quote_currency' in kwargs['initial']:
            self.fields['quote_currency'].initial = kwargs['initial']['quote_currency']

        for field_name in self.fields:
            self.fields[field_name].widget.attrs.update({'class': 'field-input'})

        if self.instance and self.instance.is_deleted:
            self.fields['title'].widget.attrs.update({'disabled': 'disabled'})

    class Meta:
        model = Requirement
        fields = [
            'user', 'title', 'nda_required', 'quote_currency', 'request_reason', 'parts',
            'end_date', 'industry', 'is_deleted', 'rfq_desc', 'file'
        ]
        widgets = {
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'field-input'}),
            'nda_required': forms.Select(choices=[(True, 'Yes'), (False, 'No')]),
            'is_deleted': forms.HiddenInput(),
            'user': forms.HiddenInput(),
        }


class RequirementPartForm(forms.ModelForm):
    class Meta:
        model = RequirementPart
        fields = ['part_name', 'Part_desc', 'technology', 'Material', 'file', 'quantity']
        widgets = {
            'part_name': forms.TextInput(attrs={'class': 'field-input'}),
            'Part_desc': forms.TextInput(attrs={'class': 'field-input'}),
            'technology': forms.Select(attrs={'class': 'field-input'}),
            'Material': forms.Select(attrs={'class': 'field-input'}),
            'file': forms.ClearableFileInput(attrs={'class': 'field-input'}),
            'quantity': forms.NumberInput(attrs={'class': 'field-input'}),
        }


RequirementPartFormSet = formset_factory(RequirementPartForm, extra=1)

# Inline formset for editing/creating RequirementParts alongside their parent
# Requirement in one submission (used by requirement/edit_requirement.html).
RequirementPartInlineFormSet = inlineformset_factory(
    Requirement,
    RequirementPart,
    form=RequirementPartForm,
    extra=1,
    can_delete=True,
)


class QuoteForm(forms.ModelForm):
    """Quote creation/edit form. Deliberately excludes `requirement` and
    `supplier` — those are assigned server-side by the view from the URL
    and the logged-in manufacturer's own profile, never taken from
    submitted form data (closing a tamper vector the old, unused
    `SelectQuote`/manual-construction pair left open)."""

    class Meta:
        model = Quote
        fields = [
            'quote_price', 'tooling_cost', 'lead_time_value', 'lead_time_unit',
            'payment_terms', 'valid_until', 'note', 'quote_file',
        ]
        widgets = {
            'quote_price': forms.NumberInput(attrs={'class': 'field-input', 'step': '0.01', 'required': 'true'}),
            'tooling_cost': forms.NumberInput(attrs={'class': 'field-input', 'step': '0.01'}),
            'lead_time_value': forms.NumberInput(attrs={'class': 'field-input'}),
            'lead_time_unit': forms.Select(attrs={'class': 'field-input'}),
            'payment_terms': forms.Select(attrs={'class': 'field-input'}),
            'valid_until': forms.DateInput(attrs={'type': 'date', 'class': 'field-input'}),
            'note': forms.Textarea(attrs={'class': 'field-input', 'rows': 4}),
            'quote_file': forms.ClearableFileInput(attrs={'class': 'field-input'}),
        }


class ShipmentForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['courier', 'courier_other', 'tracking_number', 'eway_bill_number']
        widgets = {
            'courier': forms.Select(attrs={'class': 'field-input'}),
            'courier_other': forms.TextInput(attrs={'class': 'field-input'}),
            'tracking_number': forms.TextInput(attrs={'class': 'field-input'}),
            'eway_bill_number': forms.TextInput(attrs={'class': 'field-input'}),
        }


class ProductionUpdateForm(forms.ModelForm):
    class Meta:
        model = ProductionUpdate
        fields = ['body', 'photo', 'document']
        widgets = {
            'body': forms.Textarea(attrs={'class': 'field-input', 'rows': 3, 'placeholder': "What changed on the shop floor?"}),
            'photo': forms.ClearableFileInput(attrs={'class': 'field-input'}),
            'document': forms.ClearableFileInput(attrs={'class': 'field-input'}),
        }


class RequirementQuestionForm(forms.ModelForm):
    class Meta:
        model = RequirementQuestion
        fields = ['question']
        widgets = {
            'question': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. Is a clear anodize acceptable if black is delayed?'}),
        }
