"""
Django forms for the devices app.

This module provides Django forms for device management in template-based views.
Currently includes a ModelForm for Device creation/editing.

Forms:
    - DeviceForm: Form for creating/editing Device instances
"""
from django import forms
from devices.models import Device  # Ajusta si está en otro lado

class DeviceForm(forms.ModelForm):
    """
    Django ModelForm for Device creation and editing.
    
    Provides HTML form rendering for device registration in template-based views.
    Includes device_model, serial_number, and hash fields.
    
    Fields:
        - device_model: Select dropdown of available DeviceModel instances
        - serial_number: Text input for unique device serial number
        - hash: Text input for device hash (auto-generated on save)
    
    Example usage in views:
        >>> form = DeviceForm(request.POST)
        >>> if form.is_valid():
        ...     device = form.save(commit=False)
        ...     device.owner = request.user
        ...     device.save()
    
    Note:
        Hash field is included but typically auto-generated from serial_number.
        Consider making it read-only or removing from form if not user-editable.
    """
    class Meta:
        model = Device
        fields = ['device_model', 'serial_number', 'hash']  # Ajusta se

        
        