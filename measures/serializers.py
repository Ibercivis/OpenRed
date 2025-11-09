"""
Serializers for the measures app.

This module contains DRF serializers for measurement and track models.
Provides API serialization for radiation measurements, light pollution measurements,
and uploaded track files (CSV/GPX).
"""
from rest_framework import serializers
from .models import RadiationMeasurement, LightPollutionMeasurement, Track, WeatherCache
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
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True, required=False)
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
        - mpsas, nelm, sky_temperature (light pollution readings)
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
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True, required=False)
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
    
    class Meta:
        model = Track
        fields = [
            'id', 'name', 'project', 'device', 'mission', 'mission_name', 'campaign', 'campaign_name',
            'file', 'file_type', 'description',
            'start_time', 'end_time', 'total_distance', 'average_speed', 'measurements_count',
            'min_dose_rate', 'max_dose_rate', 'avg_dose_rate', 'std_dose_rate',
            'status', 'error_message',
            'created_at', 'created_by', 'updated_at'
        ]
        read_only_fields = [
            'created_by', 'status', 'error_message', 'start_time', 'end_time', 
            'total_distance', 'average_speed', 'min_dose_rate', 'max_dose_rate', 
            'avg_dose_rate', 'std_dose_rate', 'created_at', 'updated_at', 
            'name', 'mission_name', 'campaign_name'
        ]


# Backward compatibility alias for frontend
MeasurementSerializer = RadiationMeasurementSerializer