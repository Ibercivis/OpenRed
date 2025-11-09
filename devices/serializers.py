"""
DRF serializers for the devices app.

This module provides REST Framework serializers for converting Device
and DeviceModel instances to/from JSON for API operations.

Serializers:
    - DeviceModelSerializer: Serializes device specifications
    - DeviceSerializer: Serializes individual device instances
"""
# devices/serializers.py
from rest_framework import serializers
from .models import DeviceModel, Device

class DeviceModelSerializer(serializers.ModelSerializer):
    """
    Serializer for DeviceModel (device specifications).
    
    Converts DeviceModel instances to/from JSON for API endpoints.
    Includes all fields: name, manufacturer, version, technology,
    validation status, description, picture, and max radiation range.
    
    Fields:
        All fields from DeviceModel are included.
    
    Example JSON:
        {
            "id": 1,
            "name": "GammaScout Standard",
            "manufacturer": "International Medcom",
            "version": "v2.0",
            "technology": "Geiger-Müller tube",
            "validatedByOpenRed": true,
            "description": "Professional gamma radiation detector",
            "picture": "/media/device_pictures/gammascout.jpg",
            "max_radiation_range": 1000.0
        }
    """
    class Meta:
        model = DeviceModel
        fields = '__all__'  # This will include all fields, or specify the fields you want

class DeviceSerializer(serializers.ModelSerializer):
    """
    Serializer for Device (individual device instances).
    
    Converts Device instances to/from JSON for API operations.
    Includes all fields: device_model, serial_number, hash, owner,
    purchase_date, calibration_date, and is_active status.
    
    Fields:
        All fields from Device are included.
    
    Example JSON:
        {
            "id": 5,
            "device_model": 1,
            "serial_number": "GS-12345",
            "hash": "a1b2c3d4e5f6789...",
            "owner": 3,
            "purchase_date": "2023-06-15",
            "calibration_date": "2024-01-10",
            "is_active": true
        }
    
    Note:
        The hash field is read-only and auto-generated from serial_number.
        It's included in responses but ignored in create/update requests.
    """
    class Meta:
        model = Device
        fields = '__all__'  # Include all fields, or specify the fields you want
