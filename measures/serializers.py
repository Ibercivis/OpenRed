# measures/serializers.py
from rest_framework import serializers
from .models import Measurement
from devices.models import Device  # Assuming Device is imported correctly
from django.contrib.auth.models import User

class MeasurementSerializer(serializers.ModelSerializer):
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())  # Represent the device with its ID
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)  # User can be null

    class Meta:
        model = Measurement
        fields = '__all__'  # You can specify the fields instead of using '__all__' if you want more control