# devices/serializers.py
from rest_framework import serializers
from .models import DeviceModel, Device

class DeviceModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceModel
        fields = '__all__'  # This will include all fields, or specify the fields you want

class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = '__all__'  # Include all fields, or specify the fields you want