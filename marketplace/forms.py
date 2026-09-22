from django import forms
from django.forms import formset_factory

from .models import Requirement, RequirementPart, Quote


class SelectRequirement(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'initial' in kwargs and 'quote_currency' in kwargs['initial']:
            self.fields['quote_currency'].initial = kwargs['initial']['quote_currency']

        for field_name in self.fields:
            self.fields[field_name].widget.attrs.update({'class': 'form-control'})

        if self.instance and self.instance.is_deleted:
            self.fields['title'].widget.attrs.update({'disabled': 'disabled'})

    class Meta:
        model = Requirement
        fields = [
            'user', 'title', 'nda_required', 'quote_currency', 'request_reason', 'parts',
            'end_date', 'industry', 'is_deleted', 'rfq_desc', 'file'
        ]
        widgets = {
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'nda_required': forms.Select(choices=[(True, 'Yes'), (False, 'No')]),
            'is_deleted': forms.HiddenInput(),
        }


class RequirementPartForm(forms.ModelForm):
    class Meta:
        model = RequirementPart
        fields = ['part_name', 'Part_desc', 'technology', 'Material', 'file', 'quantity']
        widgets = {
            'part_name': forms.TextInput(attrs={'class': 'form-control'}),
            'Part_desc': forms.TextInput(attrs={'class': 'form-control'}),
            'technology': forms.Select(attrs={'class': 'form-control'}),
            'Material': forms.Select(attrs={'class': 'form-control'}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
        }


RequirementPartFormSet = formset_factory(RequirementPartForm, extra=1)


class SelectQuote(forms.ModelForm):
    class Meta:
        model = Quote
        fields = [
            'requirement',
            'supplier',
            'quote_price',
            'note',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quote_price'].widget.attrs.update({'class': 'form-control', 'required': 'true'})
        self.fields['note'].widget.attrs.update({'class': 'form-control'})
