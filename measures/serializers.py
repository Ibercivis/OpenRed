"""
Serializers for the measures app.

This module contains DRF serializers for measurement and track models.
Provides API serialization for radiation measurements, light pollution measurements,
and uploaded track files (CSV/GPX).
"""
from rest_framework import serializers
from .models import RadiationMeasurement, LightPollutionMeasurement, Track, WeatherCache, Spectrum, Station
from missions.models import Project, Mission, Campaign
from devices.models import Device
from django.contrib.auth.models import User


class WeatherCacheSerializer(serializers.ModelSerializer):
    """
    Serializer for WeatherCache model.
    
    Provides weather data cached by H3 spatial cell and time window.
    """
    class Meta:
        model = WeatherCache
        fields = [
            'id', 'h3_cell', 'timestamp_hour',
            'temperature', 'humidity', 'pressure',
            'wind_speed', 'wind_direction', 'cloud_cover',
            'rain_sum',
            'fetched', 'fetch_attempts'
        ]
        read_only_fields = fields


class RadiationMeasurementSerializer(serializers.ModelSerializer):
    """
    Serializer for RadiationMeasurement model.
    
    Handles serialization of gamma radiation measurement data including
    geolocation, sensor readings, and organizational relationships.
    
    Fields:
        All RadiationMeasurement model fields including:
        - device, user, project, campaign, track (relationships)
        - dateTime, latitude, longitude, altitude (location/time)
        - cpm, dose_rate, speed (sensor readings)
        - data_quality, notes (metadata)
        - weather_cache (weather data via FK)
    
    Weather Data:
        Access via weather_cache relationship with select_related for efficiency.
        Returns nested WeatherCache data if available.
    
    Note:
        - device and project are required
        - user, campaign, and track are optional
    """
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), 
        allow_null=True, 
        required=False,
        write_only=True  # No exponer user_id en respuestas GET
    )
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    weather_cache = WeatherCacheSerializer(read_only=True)

    class Meta:
        model = RadiationMeasurement
        fields = '__all__'


class LightPollutionMeasurementSerializer(serializers.ModelSerializer):
    """
    Serializer for LightPollutionMeasurement model.
    
    Handles serialization of light pollution measurement data including
    geolocation, sky quality readings, and organizational relationships.
    
    Fields:
        All LightPollutionMeasurement model fields including:
        - device, user, project, campaign, track (relationships)
        - dateTime, latitude, longitude, altitude (location/time)
        - lux, cct, cieX/cieY/cieU/cieV, duv, tint (light pollution readings)
        - data_quality, notes (metadata)
        - weather_cache (weather data via FK)
    
    Weather Data:
        Access via weather_cache relationship with select_related for efficiency.
        Returns nested WeatherCache data if available.
    
    Note:
        - device and project are required
        - user, campaign, and track are optional
    """
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), 
        allow_null=True, 
        required=False,
        write_only=True  # No exponer user_id en respuestas GET
    )
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    weather_cache = WeatherCacheSerializer(read_only=True)

    class Meta:
        model = LightPollutionMeasurement
        fields = '__all__'


class TrackSerializer(serializers.ModelSerializer):
    """
    Serializer for Track model.
    
    Handles serialization of uploaded track files (CSV/GPX) and their metadata.
    Includes computed fields for measurement count and track name derived from filename.
    
    Fields:
        Standard fields:
        - id, project, device, mission, campaign, created_by
        - file, file_type, description
        - status, error_message
        - created_at, updated_at
        
        Computed/read-only fields:
        - name (str): Extracted from filename
        - mission_name (str): Name of the mission (if assigned)
        - campaign_name (str): Name of the campaign (if assigned)
        - measurements_count (int): Total measurements in this track
        - start_time (datetime): First measurement time
        - end_time (datetime): Last measurement time
    
    Note:
        - project and device are required
        - mission and campaign are optional
        - If campaign has a mission, track.mission will be auto-set to match
        - Status changes automatically during processing
    """
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())
    mission = serializers.PrimaryKeyRelatedField(queryset=Mission.objects.all(), allow_null=True, required=False)
    campaign = serializers.PrimaryKeyRelatedField(queryset=Campaign.objects.all(), allow_null=True, required=False)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    measurements_count = serializers.IntegerField(source='total_measurements', read_only=True)
    name = serializers.CharField(read_only=True)  # Property from filename
    
    # Computed fields with names
    mission_name = serializers.CharField(source='mission.name', read_only=True)
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    campaign_has_password = serializers.SerializerMethodField()
    
    # Password field for protected campaigns (write-only)
    campaign_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    
    class Meta:
        model = Track
        fields = [
            'id', 'name', 'project', 'device', 'mission', 'mission_name', 'campaign', 'campaign_name',
            'campaign_has_password', 'campaign_password',
            'track_type',
            'file', 'file_type', 'description',
            'start_time', 'end_time', 'total_distance', 'average_speed', 'measurements_count',
            'min_dose_rate', 'max_dose_rate', 'avg_dose_rate', 'std_dose_rate',
            'synced', 'synced_at', 'cloud_track_id', 'required_gps_accuracy_meters',
            'status', 'error_message',
            'created_at', 'created_by', 'updated_at'
        ]
        read_only_fields = [
            'created_by', 'status', 'error_message', 'start_time', 'end_time', 
            'total_distance', 'average_speed', 'min_dose_rate', 'max_dose_rate', 
            'avg_dose_rate', 'std_dose_rate', 'created_at', 'updated_at', 
            'name', 'mission_name', 'campaign_name', 'campaign_has_password'
        ]
    
    def get_campaign_has_password(self, obj):
        """Return True if campaign has a password, False otherwise."""
        return bool(obj.campaign and obj.campaign.password) if obj.campaign else False
    
    def validate(self, data):
        """
        Validate campaign password if campaign is protected.
        """
        campaign = data.get('campaign')
        campaign_password = data.pop('campaign_password', None)
        
        if campaign and campaign.password:
            # Campaign is protected, password is required
            if not campaign_password:
                raise serializers.ValidationError({
                    'campaign_password': 'Esta campaña requiere contraseña para subir tracks.'
                })
            
            # Verify password
            if campaign_password != campaign.password:
                raise serializers.ValidationError({
                    'campaign_password': 'Contraseña incorrecta.'
                })
        
        return data


class SpectrumSerializer(serializers.ModelSerializer):
    """
    Read serializer for Spectrum (gamma spectra integrated over track segments).

    Outputs camelCase keys to mirror the upload_json contract (the client
    normalizes both camelCase and snake_case). Includes `id` and the full
    `counts` array — this endpoint is only called when opening a single track,
    not in list views, so returning counts here is intentional.
    """
    startedAt = serializers.DateTimeField(source='started_at')
    endedAt = serializers.DateTimeField(source='ended_at')
    durationSec = serializers.IntegerField(source='duration_sec')
    channelCount = serializers.IntegerField(source='channel_count')
    startLat = serializers.FloatField(source='start_lat', allow_null=True)
    startLon = serializers.FloatField(source='start_lon', allow_null=True)
    startAlt = serializers.FloatField(source='start_alt', allow_null=True)
    endLat = serializers.FloatField(source='end_lat', allow_null=True)
    endLon = serializers.FloatField(source='end_lon', allow_null=True)

    class Meta:
        model = Spectrum
        fields = [
            'id', 'name', 'index',
            'startedAt', 'endedAt', 'durationSec',
            'a0', 'a1', 'a2', 'channelCount', 'counts',
            'startLat', 'startLon', 'startAlt', 'endLat', 'endLon',
        ]
        read_only_fields = fields


class StationSerializer(serializers.ModelSerializer):
    """
    Serializer for static base stations (radiation only).

    The station is bound 1:1 to a device the user owns and files all readings
    under a fixed radiation project (campaign stays NULL). Location, project and
    interval are set here once; incoming static measurements derive everything
    from the device token, so the firmware never sends them.

    Watchdog fields (``status``, ``last_measurement_at``) are read-only — they are
    maintained server-side by the gap-detection job.
    """
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Station
        fields = [
            'id', 'name', 'device', 'project', 'user',
            'latitude', 'longitude', 'altitude',
            'expected_interval_seconds',
            'last_measurement_at', 'status', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'user', 'last_measurement_at', 'status', 'created_at', 'updated_at',
        ]

    def validate_device(self, device):
        """The device must belong to the requesting user and not already be a station."""
        request = self.context.get('request')
        if request is not None and device.owner_id != request.user.id:
            raise serializers.ValidationError("Este dispositivo no te pertenece.")
        existing = Station.objects.filter(device=device)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("Este dispositivo ya tiene una estación asociada.")
        return device

    def validate_project(self, project):
        if project.project_type != 'radiation':
            raise serializers.ValidationError("Una estación solo puede asociarse a un proyecto de radiación.")
        return project


# Backward compatibility alias for frontend
MeasurementSerializer = RadiationMeasurementSerializer