from django import forms

from .models import PortalFeedback, PORTAL_FEATURES


class PortalFeedbackForm(forms.ModelForm):
    useful_features = forms.MultipleChoiceField(choices=PORTAL_FEATURES, required=False, widget=forms.CheckboxSelectMultiple(attrs={'class': 'peer sr-only'}))
    improvement_areas = forms.MultipleChoiceField(choices=PORTAL_FEATURES, required=False, widget=forms.CheckboxSelectMultiple(attrs={'class': 'peer sr-only'}))

    class Meta:
        model = PortalFeedback
        fields = ['rating', 'useful_features', 'improvement_areas', 'improvement_note', 'comment', 'allow_public']
        widgets = {
            'improvement_note': forms.Textarea(attrs={'class': 'field-input', 'rows': 3, 'placeholder': "What's missing, slow or confusing? Only our team sees this."}),
            'comment': forms.Textarea(attrs={'class': 'field-input', 'rows': 3, 'placeholder': "A sentence or two about your experience with ManufactureHub."}),
        }
        labels = {
            'improvement_note': 'Anything else we should improve?',
            'comment': 'Your review',
            'allow_public': 'Show my review, first name and company on the ManufactureHub website',
        }
