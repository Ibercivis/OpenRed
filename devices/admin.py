"""
Django admin configuration for the devices app.

This module registers Device and DeviceModel in the admin interface
for management through Django's built-in admin panel.

Admin registrations:
    - DeviceModel: Device specifications and technical details
    - Device: Individual device instances with ownership
"""
from django.contrib import admin
from .models import DeviceModel, Device

# Register your models here.
admin.site.register(DeviceModel)
admin.site.register(Device)
