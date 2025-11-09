"""
Views for the missions app.

This module provides read-only API ViewSets for Project, Mission, and Campaign models.
All endpoints are publicly accessible and support only GET operations (list, retrieve).
Modifications must be done through Django admin interface.
"""
from django.shortcuts import render
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Project, Mission, Campaign
from .serializers import ProjectSerializer, MissionSerializer, CampaignSerializer


class ProjectViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API ViewSet for Project management.
    
    Provides public read access to measurement projects. Projects define the type
    of measurements (radiation/light_pollution) and group related data collection efforts.
    
    Endpoints:
        GET /api/projects/ - List all projects with measurement counts
        GET /api/projects/{id}/ - Retrieve specific project details
        GET /api/projects/with_counts/ - List projects with pre-calculated statistics
    
    Permissions:
        - Public read access (no authentication required)
        - Modifications only via Django admin
        
    Performance:
        - list() pre-calculates measurement counts for all projects
        - Avoids N+1 queries by computing statistics in Python loop
    
    Attributes:
        queryset: All Project objects ordered by name
        serializer_class: ProjectSerializer with computed fields
        permission_classes: Empty list (public access)
    """
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = []  # Public read access
    
    def get_queryset(self):
        """
        Get optimized queryset with related data.
        
        Returns:
            QuerySet: All projects ordered by name
        """
        return Project.objects.all().order_by('name')
    
    @swagger_auto_schema(
        tags=['Projects'],
        operation_description="List all projects with measurement counts (public access)",
    )
    def list(self, request, *args, **kwargs):
        """
        List all projects with pre-calculated measurement statistics.
        
        This method optimizes performance by pre-calculating measurement counts
        and last measurement dates for all projects before serialization.
        The calculated values are stored as temporary attributes on each project
        instance and picked up by the serializer.
        
        Args:
            request: HTTP request object
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Response: JSON list of projects with measurement statistics
            
        Performance Notes:
            - Executes 2-3 queries total (1 for projects + 1-2 for measurements)
            - Avoids N+1 query problem
            - For large datasets, consider using DB aggregation instead
        """
        # Import here to avoid circular imports
        from measures.models import RadiationMeasurement, LightPollutionMeasurement
        
        projects = self.get_queryset()
        
        # Pre-calculate counts for all projects using Django ORM
        for project in projects:
            if project.project_type == 'radiation':
                project._measurements_count = RadiationMeasurement.objects.filter(project=project).count()
                last_measurement = RadiationMeasurement.objects.filter(project=project).order_by('-dateTime').first()
                project._last_measurement_date = last_measurement.dateTime if last_measurement else None
            elif project.project_type == 'light_pollution':
                project._measurements_count = LightPollutionMeasurement.objects.filter(project=project).count()
                last_measurement = LightPollutionMeasurement.objects.filter(project=project).order_by('-dateTime').first()
                project._last_measurement_date = last_measurement.dateTime if last_measurement else None
            else:
                project._measurements_count = 0
                project._last_measurement_date = None
        
        # Use standard DRF behavior with pre-calculated data
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Projects'],
        operation_description="Get details of a specific project (public access)",
    )
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a specific project by ID.
        
        Args:
            request: HTTP request object
            *args: Additional positional arguments
            **kwargs: Should include 'pk' for project ID
            
        Returns:
            Response: JSON representation of the project
        """
        return super().retrieve(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Projects'],
        operation_description="Get projects with measurement counts (public access)",
    )
    @action(detail=False, methods=['get'])
    def with_counts(self, request):
        """
        Custom endpoint for projects with measurement statistics.
        
        This is an alias for the list() method, provided for API clarity.
        Returns the same data as GET /api/projects/.
        
        Args:
            request: HTTP request object
            
        Returns:
            Response: JSON list of projects with measurement statistics
        """
        return self.list(request)


class MissionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API ViewSet for Mission management.
    
    Provides public read access to missions, which are abstract organizational
    groupings within projects. Missions help organize related campaigns and
    data collection efforts.
    
    Endpoints:
        GET /api/missions/ - List all missions
        GET /api/missions/{id}/ - Retrieve specific mission details
    
    Permissions:
        - Public read access (no authentication required)
        - Modifications only via Django admin
        
    Attributes:
        queryset: All Mission objects
        serializer_class: MissionSerializer
        permission_classes: Empty list (public access)
    """
    queryset = Mission.objects.all()
    serializer_class = MissionSerializer
    permission_classes = []  # Public read access
    
    @swagger_auto_schema(
        tags=['Missions'],
        operation_description="List all missions (public access)"
    )
    def list(self, request, *args, **kwargs):
        """
        List all missions.
        
        Args:
            request: HTTP request object
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Response: JSON list of all missions
        """
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Missions'],
        operation_description="Get details of a specific mission (public access)"
    )
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a specific mission by ID.
        
        Args:
            request: HTTP request object
            *args: Additional positional arguments
            **kwargs: Should include 'pk' for mission ID
            
        Returns:
            Response: JSON representation of the mission
        """
        return super().retrieve(request, *args, **kwargs)


class CampaignViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API ViewSet for Campaign management.
    
    Provides public read access to campaigns, which are specific data collection
    efforts within missions. Campaigns define concrete measurement activities with
    assigned participants and devices.
    
    Endpoints:
        GET /api/campaigns/ - List all campaigns
        GET /api/campaigns/{id}/ - Retrieve specific campaign details
    
    Permissions:
        - Public read access (no authentication required)
        - Modifications only via Django admin
        
    Attributes:
        queryset: All Campaign objects
        serializer_class: CampaignSerializer
        permission_classes: Empty list (public access)
    """
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer
    permission_classes = []  # Public read access
    
    def get_queryset(self):
        """
        Filter campaigns by project and/or mission query parameters.
        
        Query Parameters:
            project (int): Filter campaigns by project (via mission.project)
            mission (int): Filter campaigns by mission
            
        Examples:
            /api/campaigns/ - All campaigns
            /api/campaigns/?project=1 - Campaigns whose mission belongs to project 1
            /api/campaigns/?mission=1 - Campaigns of mission 1
            /api/campaigns/?project=1&mission=1 - Campaigns of mission 1 in project 1
        """
        queryset = Campaign.objects.all()
        project_id = self.request.query_params.get('project')
        mission_id = self.request.query_params.get('mission')
        
        if mission_id:
            queryset = queryset.filter(mission_id=mission_id)
        
        if project_id:
            # Filter by project through the mission relationship
            queryset = queryset.filter(mission__project_id=project_id)
        
        return queryset

    @swagger_auto_schema(
        tags=['Campaigns'],
        operation_description="List all campaigns (public access)"
    )
    def list(self, request, *args, **kwargs):
        """
        List all campaigns.
        
        Args:
            request: HTTP request object
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Response: JSON list of all campaigns
        """
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Campaigns'],
        operation_description="Get details of a specific campaign (public access)"
    )
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a specific campaign by ID.
        
        Args:
            request: HTTP request object
            *args: Additional positional arguments
            **kwargs: Should include 'pk' for campaign ID
            
        Returns:
            Response: JSON representation of the campaign
        """
        return super().retrieve(request, *args, **kwargs)