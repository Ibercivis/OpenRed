# devices/serializers.py
from rest_framework import serializers
from .models import Mission, Campaign

class MissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mission
        fields = '__all__'  # This will include all fields, or specify the fields you want

class CampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = '__all__'  # Include all fields, or specify the fields you want