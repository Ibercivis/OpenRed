from django import forms
from devices.models import Device  # Ajusta si está en otro lado

class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ['device_model', 'serial_number', 'hash']  # Ajusta se

        