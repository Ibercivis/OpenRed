"""
Serializers for the missions app.

This module contains DRF serializers for Project, Mission, and Campaign models.
All serializers support read-only operations via the API.
"""
from rest_framework import serializers
from .models import Project, Mission, Campaign


class ProjectSerializer(serializers.ModelSerializer):
    """
    Serializer for Project model with computed measurement statistics.
    
    Includes two calculated fields:
    - measurements_count: Total number of measurements in this project
    - last_measurement_date: DateTime of the most recent measurement
    
    These fields are computed based on project_type (radiation or light_pollution)
    and can be pre-calculated in the ViewSet for performance optimization.
    
    Fields:
        All Project model fields plus:
        - measurements_count (int, read-only): Total measurements
        - last_measurement_date (datetime, read-only): Most recent measurement date
    """
    measurements_count = serializers.SerializerMethodField()
    last_measurement_date = serializers.SerializerMethodField()
    
    class Meta:
        model = Project
        fields = '__all__'
        extra_kwargs = {
            'measurements_count': {'read_only': True},
            'last_measurement_date': {'read_only': True},
        }
    
    def get_measurements_count(self, obj):
        """
        Get the total number of measurements for this project.
        
        Returns different measurement types based on project_type:
        - 'radiation': Counts RadiationMeasurement records
        - 'light_pollution': Counts LightPollutionMeasurement records
        
        Performance: Checks for pre-calculated value from ViewSet annotations
        before falling back to a real-time database query.
        
        Args:
            obj (Project): The project instance
            
        Returns:
            int: Total number of measurements
        """
        # Import here to avoid circular imports
        from measures.models import RadiationMeasurement, LightPollutionMeasurement
        
        # Use pre-calculated value from ViewSet if available
        if hasattr(obj, '_measurements_count'):
            return obj._measurements_count
            
        # Fallback: calculate in real-time
        if obj.project_type == 'radiation':
            return RadiationMeasurement.objects.filter(project=obj).count()
        elif obj.project_type == 'light_pollution':
            return LightPollutionMeasurement.objects.filter(project=obj).count()
        return 0
    
    def get_last_measurement_date(self, obj):
        """
        Get the datetime of the most recent measurement for this project.
        
        Queries the appropriate measurement table based on project_type
        and returns the most recent dateTime value.
        
        Performance: Checks for pre-calculated value from ViewSet annotations
        before falling back to a real-time database query.
        
        Args:
            obj (Project): The project instance
            
        Returns:
            datetime or None: DateTime of last measurement, or None if no measurements exist
        """
        # Import here to avoid circular imports
        from measures.models import RadiationMeasurement, LightPollutionMeasurement
        
        # Use pre-calculated value from ViewSet if available
        if hasattr(obj, '_last_measurement_date'):
            return obj._last_measurement_date
            
        # Fallback: calculate in real-time
        if obj.project_type == 'radiation':
            last_measurement = RadiationMeasurement.objects.filter(project=obj).order_by('-dateTime').first()
        elif obj.project_type == 'light_pollution':
            last_measurement = LightPollutionMeasurement.objects.filter(project=obj).order_by('-dateTime').first()
        else:
            return None
            
        return last_measurement.dateTime if last_measurement else None


class MissionSerializer(serializers.ModelSerializer):
    """
    Serializer for Mission model.
    
    Provides read-only API access to mission data including relationships
    to parent project and child campaigns.
    
    Fields:
        All Mission model fields including:
        - id, name, description
        - project (ForeignKey ID)
        - start_date, end_date
        - created_at, created_by
    """
    class Meta:
        model = Mission
        fields = '__all__'


class CampaignSerializer(serializers.ModelSerializer):
    """
    Serializer for Campaign model.
    
    Provides read-only API access to campaign data including relationships
    to parent mission, participants, and devices.
    
    Fields:
        All Campaign model fields including:
        - id, name, description
        - mission (ForeignKey ID)
        - participants (ManyToMany IDs)
        - devices (ManyToMany IDs)
        - start_date, end_date
        - created_at, created_by
    """
    class Meta:
        model = Campaign
        fields = '__all__'