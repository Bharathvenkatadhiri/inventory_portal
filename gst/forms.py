import re

from django import forms

# 2-digit state code, 10-character PAN, entity number, literal Z, checksum.
GSTIN_PATTERN = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')


def normalize_gstin(value):
    return re.sub(r'\s+', '', value or '').upper()


class GSTINForm(forms.Form):
    """Validates a verification request before any provider is called."""
    gstin = forms.CharField(max_length=20, error_messages={'required': "Enter a GSTIN to verify."})

    def clean_gstin(self):
        gstin = normalize_gstin(self.cleaned_data['gstin'])
        if not GSTIN_PATTERN.match(gstin):
            raise forms.ValidationError("That doesn't look like a valid GSTIN. Please check and try again.")
        return gstin
