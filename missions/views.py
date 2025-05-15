from django.shortcuts import render
from rest_framework import viewsets
from .models import Mission, Campaign

# Create your views here.
from .serializers import MissionSerializer, CampaignSerializer

# Create your views here.

class MissionViewSet(viewsets.ModelViewSet):
    queryset = Mission.objects.all()
    serializer_class = MissionSerializer

class CampaignViewSet(viewsets.ModelViewSet):
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer