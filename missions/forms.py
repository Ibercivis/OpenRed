from django import forms
from django.contrib.auth.models import User
from .models import UserGroup, Campaign

class UserGroupForm(forms.ModelForm):
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Usuarios"
    )

    class Meta:
        model = UserGroup
        fields = ['name', 'description', 'users']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

class CampaignForm(forms.ModelForm):
    user_groups = forms.ModelMultipleChoiceField(
        queryset=UserGroup.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Grupos de usuarios"
    )
    
    participants = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Participantes individuales"
    )

    class Meta:
        model = Campaign
        fields = ['name', 'description', 'mission', 'participants', 'user_groups', 'devices', 'start_date', 'end_date']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }