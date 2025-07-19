# measures/serializers.py
from rest_framework import serializers
from .models import Project, RadiationMeasurement, LightPollutionMeasurement, Track
from devices.models import Device
from missions.models import Campaign
from django.contrib.auth.models import User

class ProjectSerializer(serializers.ModelSerializer):
    measurements_count = serializers.SerializerMethodField()
    last_measurement_date = serializers.SerializerMethodField()
    
    class Meta:
        model = Project
        fields = '__all__'
        # Incluir los campos calculados en los campos del serializer
        extra_kwargs = {
            'measurements_count': {'read_only': True},
            'last_measurement_date': {'read_only': True},
        }
    
    def get_measurements_count(self, obj):
        """
        Devolver el número de mediciones según el tipo de proyecto.
        Se optimiza usando una sola consulta por tipo de proyecto.
        """
        # Si ya se calculó en el ViewSet con annotations, usar ese valor
        if hasattr(obj, '_measurements_count'):
            return obj._measurements_count
            
        # Fallback: calcular en tiempo real
        if obj.project_type == 'radiation':
            return RadiationMeasurement.objects.filter(project=obj).count()
        elif obj.project_type == 'light_pollution':
            return LightPollutionMeasurement.objects.filter(project=obj).count()
        return 0
    
    def get_last_measurement_date(self, obj):
        """
        Devolver la fecha de la última medición.
        Se optimiza usando una sola consulta por tipo de proyecto.
        """
        # Si ya se calculó en el ViewSet con annotations, usar ese valor
        if hasattr(obj, '_last_measurement_date'):
            return obj._last_measurement_date
            
        # Fallback: calcular en tiempo real
        if obj.project_type == 'radiation':
            last_measurement = RadiationMeasurement.objects.filter(project=obj).order_by('-dateTime').first()
        elif obj.project_type == 'light_pollution':
            last_measurement = LightPollutionMeasurement.objects.filter(project=obj).order_by('-dateTime').first()
        else:
            return None
            
        return last_measurement.dateTime if last_measurement else None

class RadiationMeasurementSerializer(serializers.ModelSerializer):
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    class Meta:
        model = RadiationMeasurement
        fields = '__all__'

class LightPollutionMeasurementSerializer(serializers.ModelSerializer):
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    class Meta:
        model = LightPollutionMeasurement
        fields = '__all__'

class TrackSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())
    campaign = serializers.PrimaryKeyRelatedField(queryset=Campaign.objects.all())
    created_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)

    class Meta:
        model = Track
        fields = '__all__'

# Para compatibilidad temporal con el frontend
MeasurementSerializer = RadiationMeasurementSerializer