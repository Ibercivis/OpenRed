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

        The ingest token is NEVER exposed here (this serializer feeds public GET
        endpoints). Only its presence is reported via ``has_ingest_token``; the
        plaintext token is returned once, separately, on creation/rotation.
    """
    has_ingest_token = serializers.BooleanField(read_only=True)
    last_measurement_at = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = Device
        # Exclude the secret hash; keep everything else.
        exclude = ['ingest_token_hash']
        read_only_fields = ['hash', 'owner', 'ingest_token_created_at', 'created_at', 'updated_at']

    def get_last_measurement_at(self, obj):
        """Timestamp of this device's most recent measurement (radiation or light), or None."""
        # ``mine`` precomputes these in bulk to avoid two queries per device.
        bulk = self.context.get('last_measurement_by_device')
        if bulk is not None:
            return bulk.get(obj.id)

        from measures.models import RadiationMeasurement, LightPollutionMeasurement
        latest = None
        for model in (RadiationMeasurement, LightPollutionMeasurement):
            dt = (model.objects.filter(device=obj)
                  .order_by('-dateTime')
                  .values_list('dateTime', flat=True)
                  .first())
            if dt is not None and (latest is None or dt > latest):
                latest = dt
        return latest

    def get_is_owner(self, obj):
        """
        Whether the requesting user owns this device.

        A shared device (e.g. a RadiaCode lent to several volunteers) is listed
        in ``/api/devices/mine/`` for everyone who has measured with it, but only
        its owner can rotate the ingest token or edit it.
        """
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return obj.owner_id == request.user.id
