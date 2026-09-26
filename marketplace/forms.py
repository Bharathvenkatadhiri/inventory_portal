import os

from django import forms
from django.conf import settings
from django.forms import formset_factory, inlineformset_factory

from .models import Requirement, RequirementPart, Quote, Order, ProductionUpdate, Message, AmendmentResponse, SupplierReview


class SelectRequirement(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'initial' in kwargs and 'quote_currency' in kwargs['initial']:
            self.fields['quote_currency'].initial = kwargs['initial']['quote_currency']

        for field_name in self.fields:
            self.fields[field_name].widget.attrs.update({'class': 'field-input'})

    class Meta:
        model = Requirement
        # `user`, `is_deleted` and `parts` are set by the views, never taken
        # from posted data: they used to be hidden fields, so a crafted POST
        # could create or reassign an RFQ as another user.
        fields = [
            'title', 'nda_required', 'quote_currency', 'request_reason',
            'end_date', 'industry', 'rfq_desc', 'file'
        ]
        widgets = {
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'field-input'}),
            'nda_required': forms.Select(choices=[(True, 'Yes'), (False, 'No')]),
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


class SupplierReviewForm(forms.ModelForm):
    class Meta:
        model = SupplierReview
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.RadioSelect,
            'comment': forms.Textarea(attrs={'class': 'field-input', 'rows': 3, 'placeholder': "How did the order go? Quality, communication, delivery…"}),
        }


MESSAGE_ATTACHMENT_EXTENSIONS = {
    '.pdf', '.png', '.jpg', '.jpeg', '.step', '.stp', '.iges', '.igs', '.stl', '.dxf', '.dwg',
    '.xlsx', '.xls', '.csv', '.docx', '.doc', '.txt', '.zip',
}


class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ['body', 'attachment']
        widgets = {
            'body': forms.Textarea(attrs={'class': 'field-input', 'rows': 2, 'placeholder': 'Write a message...'}),
        }

    def clean_attachment(self):
        attachment = self.cleaned_data.get('attachment')
        if not attachment:
            return attachment
        ext = os.path.splitext(attachment.name)[1].lower()
        if ext not in MESSAGE_ATTACHMENT_EXTENSIONS:
            raise forms.ValidationError("That file type isn't supported. Allowed: " + ", ".join(sorted(MESSAGE_ATTACHMENT_EXTENSIONS)))
        if attachment.size > settings.MESSAGE_ATTACHMENT_MAX_BYTES:
            limit_mb = settings.MESSAGE_ATTACHMENT_MAX_BYTES // (1024 * 1024)
            raise forms.ValidationError(f"Attachments can be up to {limit_mb} MB.")
        return attachment

    def clean(self):
        cleaned_data = super().clean()
        if not (cleaned_data.get('body') or '').strip() and not cleaned_data.get('attachment'):
            raise forms.ValidationError("Write a message or attach a file.")
        return cleaned_data

    def save(self, commit=True):
        message = super().save(commit=False)
        if message.attachment:
            upload = self.cleaned_data['attachment']
            message.attachment_name = upload.name[:255]
            message.attachment_content_type = (getattr(upload, 'content_type', '') or 'application/octet-stream')[:100]
            message.attachment_size = upload.size
        if commit:
            message.save()
        return message


class AmendmentAcceptForm(forms.ModelForm):
    """Supplier accepts a buyer's RFQ changes by re-pricing their quote.
    Pre-filled from the current quote; nothing on the quote changes until
    the buyer accepts this pricing."""

    class Meta:
        model = AmendmentResponse
        fields = ['quote_price', 'tooling_cost', 'lead_time_value', 'lead_time_unit', 'payment_terms', 'valid_until', 'supplier_note']
        labels = {'quote_price': 'Unit price', 'tooling_cost': 'Tooling / setup', 'lead_time_value': 'Lead time', 'supplier_note': 'Note to buyer'}
        widgets = {
            'quote_price': forms.NumberInput(attrs={'class': 'field-input', 'step': '0.01'}),
            'tooling_cost': forms.NumberInput(attrs={'class': 'field-input', 'step': '0.01'}),
            'lead_time_value': forms.NumberInput(attrs={'class': 'field-input'}),
            'lead_time_unit': forms.Select(attrs={'class': 'field-input'}),
            'payment_terms': forms.Select(attrs={'class': 'field-input'}),
            'valid_until': forms.DateInput(attrs={'type': 'date', 'class': 'field-input'}),
            'supplier_note': forms.Textarea(attrs={'class': 'field-input', 'rows': 2, 'placeholder': 'Optional: what changed in your pricing'}),
        }

    def __init__(self, *args, quote=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quote_price'].required = True
        self.fields['tooling_cost'].required = True
        self.fields['lead_time_unit'].required = True
        if quote is not None and not self.is_bound:
            for field in AmendmentResponse.PRICING_FIELDS:
                self.initial[field] = getattr(quote, field)
