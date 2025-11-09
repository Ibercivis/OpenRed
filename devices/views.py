"""
API views for the devices app.

This module provides REST API ViewSets for device management,
including device models (specifications) and individual device instances.

ViewSets:
    - DeviceModelViewSet: Read-only access to device specifications
    - DeviceViewSet: Full CRUD for individual devices with auth control
"""
from django.shortcuts import render
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import DeviceModel, Device
from .serializers import DeviceModelSerializer, DeviceSerializer

# Create your views here.

class DeviceModelViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API ViewSet for device models (specifications).
    
    Provides access to device model catalog including technical specifications,
    manufacturer information, and validation status. Users can browse available
    device types but cannot create or modify them via API (use Django Admin).
    
    Permissions:
        - GET (list): Public - no authentication required
        - GET (retrieve): Public - no authentication required
        - POST/PUT/DELETE: Not allowed (read-only ViewSet)
    
    Endpoints:
        GET /api/device-models/ - List all device models
        GET /api/device-models/{id}/ - Get specific device model details
    
    Filtering:
        Can filter by:
        - manufacturer: ?manufacturer=International+Medcom
        - validatedByOpenRed: ?validatedByOpenRed=true
    
    Example:
        >>> # List all device models
        >>> GET /api/device-models/
        >>> 
        >>> # Get specific model
        >>> GET /api/device-models/1/
    """
    queryset = DeviceModel.objects.all()
    serializer_class = DeviceModelSerializer
    
    @swagger_auto_schema(
        tags=['Devices - Models'],
        operation_description="List all available device models"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Devices - Models'],
        operation_description="Get details of a specific device model"
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


class DeviceViewSet(viewsets.ModelViewSet):
    """
    Full CRUD API ViewSet for individual devices.
    
    Provides complete device management with permission controls:
    - Read operations (GET) are public
    - Write operations (POST/PUT/PATCH/DELETE) require authentication
    
    Permissions:
        - GET (list/retrieve): Public - anyone can view devices
        - POST (create): Requires authentication - creates device owned by current user
        - PUT/PATCH (update): Requires authentication - only owner or admin can update
        - DELETE (destroy): Requires authentication - only owner or admin can delete
    
    Endpoints:
        GET /api/devices/ - List all devices
        POST /api/devices/ - Register new device (auth required)
        GET /api/devices/{id}/ - Get device details
        PUT /api/devices/{id}/ - Update device completely (auth required)
        PATCH /api/devices/{id}/ - Update device partially (auth required)
        DELETE /api/devices/{id}/ - Delete device (auth required)
    
    Filtering:
        Can filter by:
        - owner: ?owner=3
        - device_model: ?device_model=1
        - is_active: ?is_active=true
        - serial_number: ?serial_number=GS-12345
    
    Ordering:
        - By serial number (default): ?ordering=serial_number
        - By purchase date: ?ordering=-purchase_date
        - By calibration date: ?ordering=calibration_date
    
    Example Create:
        >>> POST /api/devices/
        >>> Authorization: Token abc123...
        >>> {
        ...     "device_model": 1,
        ...     "serial_number": "GS-12345",
        ...     "calibration_date": "2024-01-15",
        ...     "is_active": true
        ... }
    
    Response:
        >>> {
        ...     "id": 5,
        ...     "device_model": 1,
        ...     "serial_number": "GS-12345",
        ...     "hash": "a1b2c3d4e5f6...",
        ...     "owner": 3,
        ...     "calibration_date": "2024-01-15",
        ...     "is_active": true
        ... }
    
    Note:
        The device hash is auto-generated from serial_number on save.
        Owner is automatically set to the authenticated user on creation.
    """
    queryset = Device.objects.all()
    serializer_class = DeviceSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    
    def get_queryset(self):
        """
        For write operations (update, partial_update, destroy), filter by owner.
        Only owners can modify/delete their own devices.
        """
        queryset = super().get_queryset()
        
        if self.action in ['update', 'partial_update', 'destroy']:
            if self.request.user.is_authenticated:
                return queryset.filter(owner=self.request.user)
            return queryset.none()
        return queryset
    
    def perform_create(self, serializer):
        """
        Auto-assign the current user as owner when creating a device
        """
        serializer.save(owner=self.request.user)
    
    @swagger_auto_schema(
        tags=['Devices'],
        operation_description="List all devices (public access)"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Devices'],
        operation_description="Create a new device (requires authentication)",
        security=[{'Token': []}],
        responses={
            201: DeviceSerializer,
            400: 'Invalid data',
            401: 'Not authenticated'
        }
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Devices'],
        operation_description="Get details of a specific device (public access)"
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Devices'],
        operation_description="Update a device completely (requires authentication)",
        security=[{'Token': []}],
        responses={
            200: DeviceSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Device not found'
        }
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Devices'],
        operation_description="Partially update a device (requires authentication)",
        security=[{'Token': []}],
        responses={
            200: DeviceSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Device not found'
        }
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Devices'],
        operation_description="Delete a device (requires authentication)",
        security=[{'Token': []}],
        responses={
            204: 'Device deleted successfully',
            401: 'Not authenticated',
            404: 'Device not found'
        }
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
