from django.shortcuts import render
from rest_framework import viewsets
from .models import DeviceModel, Device
from .serializers import DeviceModelSerializer, DeviceSerializer

# Create your views here.

class DeviceModelViewSet(viewsets.ModelViewSet):
    queryset = DeviceModel.objects.all()
    serializer_class = DeviceModelSerializer

class DeviceViewSet(viewsets.ModelViewSet):
    queryset = Device.objects.all()
    serializer_class = DeviceSerializer
