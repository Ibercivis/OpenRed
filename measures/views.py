"""
Views for the measures app.

This module provides API ViewSets for measurement data and track file uploads.
Includes permission handling for public read access and authenticated write operations.

ViewSets:
    - RadiationMeasurementViewSet: CRUD for gamma radiation measurements with H3 aggregation
    - LightPollutionMeasurementViewSet: CRUD for light pollution measurements with H3 aggregation
    - TrackViewSet: Upload and manage measurement track files (CSV/GPX)
"""
from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAuthenticated
from django.contrib.gis.geos import Point
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.db.models import Q, Avg, Min, Max, Count
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from datetime import datetime, timedelta
from django.utils import timezone
from django.conf import settings
import django_rq
import h3
from django.db.models import Q
import csv
import io
import django_rq
import h3
import logging
from datetime import datetime
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.db import connection
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import RadiationMeasurement, LightPollutionMeasurement, Track
from missions.models import Project
from devices.models import Device
from .serializers import (
    RadiationMeasurementSerializer, 
    LightPollutionMeasurementSerializer, 
    TrackSerializer,
    MeasurementSerializer  # Backward compatibility alias
)

logger = logging.getLogger(__name__)


class RadiationMeasurementViewSet(viewsets.ModelViewSet):
    """
    API ViewSet for gamma radiation measurements.
    
    Provides full CRUD operations with permission controls:
    - GET (list/retrieve): Public access - anyone can view measurements
    - POST (create): Requires authentication - auto-assigns current user
    - PUT/PATCH (update): Requires authentication and ownership
    - DELETE (destroy): Requires authentication and ownership
    
    Users can only modify/delete their own measurements.
    
    Endpoints:
        GET /api/radiation-measurements/ - List all measurements
        POST /api/radiation-measurements/ - Create new measurement (auth required)
        GET /api/radiation-measurements/{id}/ - Retrieve specific measurement
        PUT /api/radiation-measurements/{id}/ - Update measurement (auth + ownership)
        PATCH /api/radiation-measurements/{id}/ - Partial update (auth + ownership)
        DELETE /api/radiation-measurements/{id}/ - Delete measurement (auth + ownership)
    
    Attributes:
        queryset: All RadiationMeasurement objects with weather_cache selected
        serializer_class: RadiationMeasurementSerializer
    """
    queryset = RadiationMeasurement.objects.select_related('weather_cache').all()
    serializer_class = RadiationMeasurementSerializer

    def get_permissions(self):
        """
        GET is public, write operations require authentication
        """
        if self.action in ['list', 'retrieve', 'h3_aggregation', 'count', 'paginated']:
            return []
        return [IsAuthenticated()]

    def get_queryset(self):
        """
        For write operations (update, partial_update, destroy), filter by user ownership.
        This way users can only modify/delete their own measurements.
        If a user tries to access another user's measurement, they'll get a 404.
        
        Queryset already includes select_related('weather_cache') for optimization.
        """
        queryset = super().get_queryset()
        
        # Detectar si es una vista falsa de Swagger
        if getattr(self, 'swagger_fake_view', False):
            return queryset.none()
        
        if self.action in ['update', 'partial_update', 'destroy']:
            return queryset.filter(user=self.request.user)
        return queryset

    def perform_create(self, serializer):
        """
        Auto-assign the current authenticated user when creating a radiation measurement.
        Also enqueue weather fetching task for this measurement.
        """
        instance = serializer.save(user=self.request.user)
        
        # ✅ Enqueue weather fetching for this single measurement
        try:
            queue = django_rq.get_queue('openred-weather')
            queue.enqueue(
                'measures.tasks.fetch_pending_weather',
                limit=10,  # Process a small batch including this measurement
                max_attempts=3,
                job_timeout='5m',
                result_ttl=3600,
                job_id=f'weather_single_rad_{instance.id}_{int(timezone.now().timestamp())}'
            )
        except Exception as e:
            # Don't fail the measurement creation if weather queueing fails
            print(f"⚠️ Failed to enqueue weather task for radiation measurement {instance.id}: {e}")
        
        return instance

    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="List all radiation measurements (public access)",
        manual_parameters=[
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('mission', openapi.IN_QUERY, description="Filter by mission ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('device', openapi.IN_QUERY, description="Filter by device ID", type=openapi.TYPE_INTEGER),
        ]
    )
    def list(self, request, *args, **kwargs):
        import time
        start_time = time.time()
        start_queries = len(connection.queries)
        
        queryset = self.get_queryset()
        
        # Apply filters from query parameters
        track_id = request.query_params.get('track')
        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        device_id = request.query_params.get('device')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        # Bounding box filters
        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east = request.query_params.get('east')
        west = request.query_params.get('west')
        
        if track_id:
            queryset = queryset.filter(track_id=track_id)
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if mission_id:
            queryset = queryset.filter(campaign__mission_id=mission_id)
        if campaign_id:
            queryset = queryset.filter(campaign_id=campaign_id)
        if device_id:
            queryset = queryset.filter(device_id=device_id)
        
        # Date range filters
        if start_date:
            from django.utils.dateparse import parse_date
            parsed_date = parse_date(start_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__gte=timezone.make_aware(datetime.combine(parsed_date, datetime.min.time())))
        
        if end_date:
            from django.utils.dateparse import parse_date
            parsed_date = parse_date(end_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__lte=timezone.make_aware(datetime.combine(parsed_date, datetime.max.time())))
        
        # Bounding box filters
        if north:
            try:
                queryset = queryset.filter(latitude__lte=float(north))
            except ValueError:
                pass
        
        if south:
            try:
                queryset = queryset.filter(latitude__gte=float(south))
            except ValueError:
                pass
        
        if east:
            try:
                queryset = queryset.filter(longitude__lte=float(east))
            except ValueError:
                pass
        
        if west:
            try:
                queryset = queryset.filter(longitude__gte=float(west))
            except ValueError:
                pass
        
        # Apply pagination
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
        else:
            serializer = self.get_serializer(queryset, many=True)
            response = Response(serializer.data)
        
        # Performance metrics
        end_time = time.time()
        total_queries = len(connection.queries) - start_queries
        elapsed_ms = (end_time - start_time) * 1000
        result_count = queryset.count()
        
        logger.info(f"📊 GET /api/radiation-measurements/ | "
                   f"Time: {elapsed_ms:.2f}ms | "
                   f"SQL Queries: {total_queries} | "
                   f"Results: {result_count} | "
                   f"Filters: track={track_id}, project={project_id}, device={device_id}")
        
        return response
    
    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Create a new radiation measurement (requires authentication)",
        security=[{'Token': []}],
        responses={
            201: RadiationMeasurementSerializer,
            400: 'Invalid data',
            401: 'Not authenticated'
        }
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Get details of a specific radiation measurement (public access)"
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Update a radiation measurement completely (requires authentication and ownership - users can only update their own measurements)",
        security=[{'Token': []}],
        responses={
            200: RadiationMeasurementSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Measurement not found or not owned by user'
        }
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Partially update a radiation measurement (requires authentication and ownership - users can only update their own measurements)",
        security=[{'Token': []}],
        responses={
            200: RadiationMeasurementSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Measurement not found or not owned by user'
        }
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Delete a radiation measurement (requires authentication and ownership - users can only delete their own measurements)",
        security=[{'Token': []}],
        responses={
            204: 'Measurement deleted successfully',
            401: 'Not authenticated',
            404: 'Measurement not found or not owned by user'
        }
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Aggregate radiation measurements by H3 hexagons with statistics",
        manual_parameters=[
            openapi.Parameter('resolution', openapi.IN_QUERY, description="H3 resolution (0-15, default: 8)", type=openapi.TYPE_INTEGER, default=8),
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('min_count', openapi.IN_QUERY, description="Minimum measurements per hexagon (default: 1)", type=openapi.TYPE_INTEGER, default=1),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter measurements from this date (format: YYYY-MM-DD, example: 2024-01-15)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter measurements until this date (format: YYYY-MM-DD, example: 2024-12-31)", type=openapi.TYPE_STRING),
        ],
        responses={
            200: openapi.Response(
                description="H3 hexagon aggregation with statistics",
                examples={
                    "application/json": {
                        "resolution": 8,
                        "hexagons": [
                            {
                                "h3_index": "88283082b9fffff",
                                "avg_value": 0.15,
                                "min_value": 0.10,
                                "max_value": 0.25,
                                "std_value": 0.04,
                                "measurement_count": 42
                            }
                        ],
                        "total_hexagons": 156,
                        "total_measurements": 1234,
                        "statistics": {
                            "global_avg": 0.15,
                            "global_min": 0.05,
                            "global_max": 0.85
                        }
                    }
                }
            ),
            400: 'Invalid parameters'
        }
    )
    @action(detail=False, methods=['get'], url_path='h3-aggregation')
    def h3_aggregation(self, request):
        """
        Aggregate radiation measurements by H3 hexagons using PostgreSQL.
        
        This method delegates all H3 calculations to PostgreSQL for optimal performance.
        Uses h3-pg extension for native H3 operations in the database.
        
        Query Parameters:
            resolution (int): H3 resolution level (0-15). Default: 8
                - 0: Very large hexagons (~1000km edge)
                - 5: ~20km edge
                - 8: ~500m edge (default)
                - 10: ~60m edge
                - 15: ~0.5m edge
            
            project (int): Filter by project ID (optional)
            campaign (int): Filter by campaign ID (optional)
            track (int): Filter by track ID (optional)
            min_count (int): Minimum measurements per hexagon (default: 1)
        
        Returns:
            Response: JSON with H3 aggregation data including hexagon boundaries
        """
        # Validate resolution parameter
        try:
            resolution = int(request.query_params.get('resolution', 8))
            if not 0 <= resolution <= 15:
                return Response(
                    {'error': 'Resolution must be between 0 and 15'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except ValueError:
            return Response(
                {'error': 'Invalid resolution parameter'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate min_count parameter
        try:
            min_count = int(request.query_params.get('min_count', 1))
        except ValueError:
            min_count = 1
        
        # Build dynamic WHERE clause for filters
        where_clauses = ["latitude IS NOT NULL", "longitude IS NOT NULL", "dose_rate IS NOT NULL"]
        params = {'resolution': resolution, 'min_count': min_count}
        
        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        track_id = request.query_params.get('track')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        # Bounding box filters
        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east = request.query_params.get('east')
        west = request.query_params.get('west')
        
        if project_id:
            where_clauses.append("project_id = %(project_id)s")
            params['project_id'] = project_id
        
        if mission_id:
            # Filter by mission through campaign
            where_clauses.append("campaign_id IN (SELECT id FROM missions_campaign WHERE mission_id = %(mission_id)s)")
            params['mission_id'] = mission_id
        
        if campaign_id:
            where_clauses.append("campaign_id = %(campaign_id)s")
            params['campaign_id'] = campaign_id
        
        if track_id:
            where_clauses.append("track_id = %(track_id)s")
            params['track_id'] = track_id
        
        # Bounding box filters (north, south, east, west)
        if north is not None:
            try:
                north_val = float(north)
                where_clauses.append("latitude <= %(north)s")
                params['north'] = north_val
            except ValueError:
                return Response(
                    {'error': 'Invalid north parameter. Must be a valid latitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if south is not None:
            try:
                south_val = float(south)
                where_clauses.append("latitude >= %(south)s")
                params['south'] = south_val
            except ValueError:
                return Response(
                    {'error': 'Invalid south parameter. Must be a valid latitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if east is not None:
            try:
                east_val = float(east)
                where_clauses.append("longitude <= %(east)s")
                params['east'] = east_val
            except ValueError:
                return Response(
                    {'error': 'Invalid east parameter. Must be a valid longitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if west is not None:
            try:
                west_val = float(west)
                where_clauses.append("longitude >= %(west)s")
                params['west'] = west_val
            except ValueError:
                return Response(
                    {'error': 'Invalid west parameter. Must be a valid longitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Date filters (format: YYYY-MM-DD)
        if start_date:
            try:
                from django.utils.dateparse import parse_date
                # Parse date string (YYYY-MM-DD)
                parsed_date = parse_date(start_date)
                if parsed_date:
                    # Start from 00:00:00 of the start date
                    parsed_date = timezone.make_aware(datetime.combine(parsed_date, datetime.min.time()))
                    where_clauses.append('"dateTime" >= %(start_date)s')
                    params['start_date'] = parsed_date
                else:
                    return Response(
                        {'error': 'Invalid start_date format. Use format: YYYY-MM-DD (example: 2024-01-15)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                return Response(
                    {'error': f'Invalid start_date: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if end_date:
            try:
                from django.utils.dateparse import parse_date
                # Parse date string (YYYY-MM-DD)
                parsed_date = parse_date(end_date)
                if parsed_date:
                    # End at 23:59:59.999999 of the end date
                    parsed_date = timezone.make_aware(datetime.combine(parsed_date, datetime.max.time()))
                    where_clauses.append('"dateTime" <= %(end_date)s')
                    params['end_date'] = parsed_date
                else:
                    return Response(
                        {'error': 'Invalid end_date format. Use format: YYYY-MM-DD (example: 2024-12-31)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                return Response(
                    {'error': f'Invalid end_date: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        where_sql = " AND ".join(where_clauses)
        
        # SQL query that does ALL processing in PostgreSQL
        # Uses h3-pg extension functions for native H3 operations
        sql = f"""
        WITH h3_cells AS (
            -- Convert each measurement to its H3 cell
            SELECT 
                h3_lat_lng_to_cell(POINT(longitude, latitude), %(resolution)s) as h3_index,
                dose_rate as value
            FROM measures_radiation_measurement
            WHERE {where_sql}
        ),
        aggregated AS (
            SELECT 
                h3_index,
                COUNT(*)::int as measurement_count,
                AVG(value)::float as avg_value,
                MIN(value)::float as min_value,
                MAX(value)::float as max_value,
                STDDEV(value)::float as std_value
            FROM h3_cells
            GROUP BY h3_index
            HAVING COUNT(*) >= %(min_count)s
        )
        SELECT 
            h3_index,
            measurement_count,
            ROUND(avg_value::numeric, 6)::float as avg_value,
            ROUND(min_value::numeric, 6)::float as min_value,
            ROUND(max_value::numeric, 6)::float as max_value,
            ROUND(COALESCE(std_value, 0)::numeric, 6)::float as std_value
        FROM aggregated
        ORDER BY measurement_count DESC, avg_value DESC;
        """
        
        # Execute query using raw SQL
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [col[0] for col in cursor.description]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        # Calculate global statistics
        if results:
            all_avg_values = [r['avg_value'] for r in results]
            statistics = {
                'global_avg': round(sum(all_avg_values) / len(all_avg_values), 6),
                'global_min': round(min(r['min_value'] for r in results), 6),
                'global_max': round(max(r['max_value'] for r in results), 6),
            }
            total_measurements = sum(r['measurement_count'] for r in results)
        else:
            statistics = None
            total_measurements = 0
        
        logger.info(
            f"H3 aggregation (PostgreSQL): resolution={resolution}, "
            f"hexagons={len(results)}, measurements={total_measurements}"
        )
        
        return Response({
            'resolution': resolution,
            'hexagons': results,
            'total_hexagons': len(results),
            'total_measurements': total_measurements,
            'statistics': statistics
        })

    @action(detail=False, methods=['get'], url_path='h3-aggregation-vertex')
    def h3_aggregation_vertex(self, request):
        """
        Aggregate radiation measurements by H3 hexagons with vertex coordinates.
        
        Same as h3_aggregation but includes hexagon boundary vertices for rendering.
        
        Query Parameters:
            Same as h3_aggregation endpoint
        
        Returns:
            Response: JSON with H3 aggregation data plus vertex coordinates for each hexagon
        """
        # Reuse the same logic as h3_aggregation
        response = self.h3_aggregation(request)
        
        if response.status_code != 200:
            return response
        
        data = response.data
        hexagons = data.get('hexagons', [])
        
        # Add vertices to each hexagon using h3-py
        for hexagon in hexagons:
            h3_index = hexagon['h3_index']
            try:
                # Get vertices as lat/lng coordinates
                vertices = h3.cell_to_boundary(h3_index)
                # Convert to list of [lat, lng] pairs
                hexagon['vertices'] = [[lat, lng] for lat, lng in vertices]
            except Exception as e:
                logger.warning(f"Failed to get vertices for H3 cell {h3_index}: {e}")
                hexagon['vertices'] = []
        
        return Response(data)

    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Get the total count of radiation measurements (public access)"
    )
    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones de radiación
        """
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        return Response({'total': total})

    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Get paginated radiation measurements with progress metadata (public access)",
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER, default=1),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Items per page (max 15000)", type=openapi.TYPE_INTEGER, default=1000),
        ]
    )
    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Endpoint para obtener mediciones de radiación paginadas con metadata de progreso
        """
        page = request.GET.get('page', 1)
        page_size = request.GET.get('page_size', 1000)

        try:
            page = int(page)
            page_size = int(page_size)
            # Aumentar el límite máximo para permitir chunks más grandes
            page_size = min(page_size, 15000)  # Límite máximo más alto
        except ValueError:
            return Response({'error': 'Invalid page or page_size parameter'}, status=400)

        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()

        paginator = Paginator(queryset, page_size)

        if page > paginator.num_pages:
            return Response({
                'results': [],
                'count': total,
                'num_pages': paginator.num_pages,
                'current_page': page,
                'page_size': page_size,
                'has_next': False,
                'has_previous': page > 1,
                'loaded_so_far': total,
                'error': 'Page beyond available pages'
            }, status=200)  # Cambiar a 200 para mejor manejo

        page_obj = paginator.get_page(page)
        serializer = self.get_serializer(page_obj, many=True)

        # Calcular correctamente los elementos cargados hasta ahora
        items_in_current_page = len(serializer.data)
        loaded_so_far = ((page - 1) * page_size) + items_in_current_page

        return Response({
            'results': serializer.data,
            'count': total,
            'num_pages': paginator.num_pages,
            'current_page': page,
            'page_size': page_size,
            'items_in_page': items_in_current_page,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
            'loaded_so_far': loaded_so_far
        })

class LightPollutionMeasurementViewSet(viewsets.ModelViewSet):
    """
    ViewSet for light pollution measurements.
    - GET: Public access (anyone can view all measurements)
    - POST: Requires authentication (auto-assigns current user)
    - PUT/PATCH/DELETE: Requires authentication and ownership (users can only modify/delete their own measurements)
    """
    queryset = LightPollutionMeasurement.objects.select_related('weather_cache').all()
    serializer_class = LightPollutionMeasurementSerializer

    def get_permissions(self):
        """
        GET is public, write operations require authentication
        """
        if self.action in ['list', 'retrieve', 'h3_aggregation', 'count', 'paginated']:
            return []
        return [IsAuthenticated()]

    def get_queryset(self):
        """
        For write operations (update, partial_update, destroy), filter by user ownership.
        This way users can only modify/delete their own measurements.
        If a user tries to access another user's measurement, they'll get a 404.
        
        Queryset already includes select_related('weather_cache') for optimization.
        """
        queryset = super().get_queryset()
        
        # Detectar si es una vista falsa de Swagger
        if getattr(self, 'swagger_fake_view', False):
            return queryset.none()
        
        if self.action in ['update', 'partial_update', 'destroy']:
            return queryset.filter(user=self.request.user)
        return queryset

    def perform_create(self, serializer):
        """
        Auto-assign the current authenticated user when creating a light pollution measurement.
        Also enqueue weather fetching task for this measurement.
        """
        instance = serializer.save(user=self.request.user)
        
        # ✅ Enqueue weather fetching for this single measurement
        try:
            queue = django_rq.get_queue('openred-weather')
            queue.enqueue(
                'measures.tasks.fetch_pending_weather',
                limit=10,  # Process a small batch including this measurement
                max_attempts=3,
                job_timeout='5m',
                result_ttl=3600,
                job_id=f'weather_single_light_{instance.id}_{int(timezone.now().timestamp())}'
            )
        except Exception as e:
            # Don't fail the measurement creation if weather queueing fails
            print(f"⚠️ Failed to enqueue weather task for light pollution measurement {instance.id}: {e}")
        
        return instance

    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="List all light pollution measurements (public access)",
        manual_parameters=[
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('mission', openapi.IN_QUERY, description="Filter by mission ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('device', openapi.IN_QUERY, description="Filter by device ID", type=openapi.TYPE_INTEGER),
        ]
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        
        # Apply filters from query parameters
        track_id = request.query_params.get('track')
        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        device_id = request.query_params.get('device')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        # Bounding box filters
        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east = request.query_params.get('east')
        west = request.query_params.get('west')
        
        if track_id:
            queryset = queryset.filter(track_id=track_id)
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if mission_id:
            queryset = queryset.filter(campaign__mission_id=mission_id)
        if campaign_id:
            queryset = queryset.filter(campaign_id=campaign_id)
        if device_id:
            queryset = queryset.filter(device_id=device_id)
        
        # Date range filters
        if start_date:
            from django.utils.dateparse import parse_date
            parsed_date = parse_date(start_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__gte=timezone.make_aware(datetime.combine(parsed_date, datetime.min.time())))
        
        if end_date:
            from django.utils.dateparse import parse_date
            parsed_date = parse_date(end_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__lte=timezone.make_aware(datetime.combine(parsed_date, datetime.max.time())))
        
        # Bounding box filters
        if north:
            try:
                queryset = queryset.filter(latitude__lte=float(north))
            except ValueError:
                pass
        
        if south:
            try:
                queryset = queryset.filter(latitude__gte=float(south))
            except ValueError:
                pass
        
        if east:
            try:
                queryset = queryset.filter(longitude__lte=float(east))
            except ValueError:
                pass
        
        if west:
            try:
                queryset = queryset.filter(longitude__gte=float(west))
            except ValueError:
                pass
        
        # Apply pagination
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Create a new light pollution measurement (requires authentication)",
        security=[{'Token': []}],
        responses={
            201: LightPollutionMeasurementSerializer,
            400: 'Invalid data',
            401: 'Not authenticated'
        }
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Get details of a specific light pollution measurement (public access)"
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Update a light pollution measurement completely (requires authentication and ownership - users can only update their own measurements)",
        security=[{'Token': []}],
        responses={
            200: LightPollutionMeasurementSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Measurement not found or not owned by user'
        }
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Partially update a light pollution measurement (requires authentication and ownership - users can only update their own measurements)",
        security=[{'Token': []}],
        responses={
            200: LightPollutionMeasurementSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Measurement not found or not owned by user'
        }
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Delete a light pollution measurement (requires authentication and ownership - users can only delete their own measurements)",
        security=[{'Token': []}],
        responses={
            204: 'Measurement deleted successfully',
            401: 'Not authenticated',
            404: 'Measurement not found or not owned by user'
        }
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Aggregate light pollution measurements by H3 hexagons with statistics",
        manual_parameters=[
            openapi.Parameter('resolution', openapi.IN_QUERY, description="H3 resolution (0-15, default: 8)", type=openapi.TYPE_INTEGER, default=8),
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('min_count', openapi.IN_QUERY, description="Minimum measurements per hexagon (default: 1)", type=openapi.TYPE_INTEGER, default=1),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter measurements from this date (format: YYYY-MM-DD, example: 2024-01-15)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter measurements until this date (format: YYYY-MM-DD, example: 2024-12-31)", type=openapi.TYPE_STRING),
        ],
        responses={
            200: openapi.Response(
                description="H3 hexagon aggregation with statistics",
                examples={
                    "application/json": {
                        "resolution": 8,
                        "hexagons": [
                            {
                                "h3_index": "88283082b9fffff",
                                "avg_value": 21.5,
                                "min_value": 20.0,
                                "max_value": 23.0,
                                "std_value": 0.8,
                                "measurement_count": 42
                            }
                        ],
                        "total_hexagons": 156,
                        "total_measurements": 1234,
                        "statistics": {
                            "global_avg": 21.5,
                            "global_min": 18.0,
                            "global_max": 25.0
                        }
                    }
                }
            ),
            400: 'Invalid parameters'
        }
    )
    @action(detail=False, methods=['get'], url_path='h3-aggregation')
    def h3_aggregation(self, request):
        """
        Aggregate light pollution measurements by H3 hexagons using PostgreSQL.
        
        This method delegates all H3 calculations to PostgreSQL for optimal performance.
        Uses h3-pg extension for native H3 operations in the database.
        
        Query Parameters:
            resolution (int): H3 resolution level (0-15). Default: 8
                - 0: Very large hexagons (~1000km edge)
                - 5: ~20km edge
                - 8: ~500m edge (default)
                - 10: ~60m edge
                - 15: ~0.5m edge
            
            project (int): Filter by project ID (optional)
            campaign (int): Filter by campaign ID (optional)
            track (int): Filter by track ID (optional)
            min_count (int): Minimum measurements per hexagon (default: 1)
        
        Returns:
            Response: JSON with H3 aggregation data including hexagon boundaries
        """
        # Validate resolution parameter
        try:
            resolution = int(request.query_params.get('resolution', 8))
            if not 0 <= resolution <= 15:
                return Response(
                    {'error': 'Resolution must be between 0 and 15'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except ValueError:
            return Response(
                {'error': 'Invalid resolution parameter'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate min_count parameter
        try:
            min_count = int(request.query_params.get('min_count', 1))
        except ValueError:
            min_count = 1
        
        # Build dynamic WHERE clause for filters
        where_clauses = ["latitude IS NOT NULL", "longitude IS NOT NULL", "lux IS NOT NULL"]
        params = {'resolution': resolution, 'min_count': min_count}
        
        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        track_id = request.query_params.get('track')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        # Bounding box filters
        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east = request.query_params.get('east')
        west = request.query_params.get('west')
        
        if project_id:
            where_clauses.append("project_id = %(project_id)s")
            params['project_id'] = project_id
        
        if mission_id:
            # Filter by mission through campaign
            where_clauses.append("campaign_id IN (SELECT id FROM missions_campaign WHERE mission_id = %(mission_id)s)")
            params['mission_id'] = mission_id
        
        if campaign_id:
            where_clauses.append("campaign_id = %(campaign_id)s")
            params['campaign_id'] = campaign_id
        
        if track_id:
            where_clauses.append("track_id = %(track_id)s")
            params['track_id'] = track_id
        
        # Bounding box filters (north, south, east, west)
        if north is not None:
            try:
                north_val = float(north)
                where_clauses.append("latitude <= %(north)s")
                params['north'] = north_val
            except ValueError:
                return Response(
                    {'error': 'Invalid north parameter. Must be a valid latitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if south is not None:
            try:
                south_val = float(south)
                where_clauses.append("latitude >= %(south)s")
                params['south'] = south_val
            except ValueError:
                return Response(
                    {'error': 'Invalid south parameter. Must be a valid latitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if east is not None:
            try:
                east_val = float(east)
                where_clauses.append("longitude <= %(east)s")
                params['east'] = east_val
            except ValueError:
                return Response(
                    {'error': 'Invalid east parameter. Must be a valid longitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if west is not None:
            try:
                west_val = float(west)
                where_clauses.append("longitude >= %(west)s")
                params['west'] = west_val
            except ValueError:
                return Response(
                    {'error': 'Invalid west parameter. Must be a valid longitude.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Date filters (format: YYYY-MM-DD)
        if start_date:
            try:
                from django.utils.dateparse import parse_date
                # Parse date string (YYYY-MM-DD)
                parsed_date = parse_date(start_date)
                if parsed_date:
                    # Start from 00:00:00 of the start date
                    parsed_date = timezone.make_aware(datetime.combine(parsed_date, datetime.min.time()))
                    where_clauses.append('"dateTime" >= %(start_date)s')
                    params['start_date'] = parsed_date
                else:
                    return Response(
                        {'error': 'Invalid start_date format. Use format: YYYY-MM-DD (example: 2024-01-15)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                return Response(
                    {'error': f'Invalid start_date: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if end_date:
            try:
                from django.utils.dateparse import parse_date
                # Parse date string (YYYY-MM-DD)
                parsed_date = parse_date(end_date)
                if parsed_date:
                    # End at 23:59:59.999999 of the end date
                    parsed_date = timezone.make_aware(datetime.combine(parsed_date, datetime.max.time()))
                    where_clauses.append('"dateTime" <= %(end_date)s')
                    params['end_date'] = parsed_date
                else:
                    return Response(
                        {'error': 'Invalid end_date format. Use format: YYYY-MM-DD (example: 2024-12-31)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                return Response(
                    {'error': f'Invalid end_date: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        where_sql = " AND ".join(where_clauses)
        
        # SQL query that does ALL processing in PostgreSQL
        # Uses h3-pg extension functions for native H3 operations
        sql = f"""
        WITH h3_cells AS (
            -- Convert each measurement to its H3 cell
            SELECT 
                h3_lat_lng_to_cell(
                    POINT(longitude::float, latitude::float),
                    %(resolution)s
                ) as h3_index,
                lux::float as value
            FROM measures_light_pollution_measurement
            WHERE {where_sql}
        ),
        aggregated AS (
            -- Aggregate measurements by H3 cell
            SELECT 
                h3_index,
                COUNT(*)::int as measurement_count,
                AVG(value)::float as avg_value,
                MIN(value)::float as min_value,
                MAX(value)::float as max_value,
                STDDEV(value)::float as std_value
            FROM h3_cells
            GROUP BY h3_index
            HAVING COUNT(*) >= %(min_count)s
        )
        SELECT 
            h3_index,
            measurement_count,
            ROUND(avg_value::numeric, 6)::float as avg_value,
            ROUND(min_value::numeric, 6)::float as min_value,
            ROUND(max_value::numeric, 6)::float as max_value,
            ROUND(COALESCE(std_value, 0)::numeric, 6)::float as std_value
        FROM aggregated
        ORDER BY measurement_count DESC, avg_value DESC;
        """
        
        # Execute query using raw SQL
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [col[0] for col in cursor.description]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        # Calculate global statistics
        if results:
            all_avg_values = [r['avg_value'] for r in results]
            statistics = {
                'global_avg': round(sum(all_avg_values) / len(all_avg_values), 6),
                'global_min': round(min(r['min_value'] for r in results), 6),
                'global_max': round(max(r['max_value'] for r in results), 6),
            }
            total_measurements = sum(r['measurement_count'] for r in results)
        else:
            statistics = None
            total_measurements = 0
        
        logger.info(
            f"H3 aggregation (light pollution, PostgreSQL): resolution={resolution}, "
            f"hexagons={len(results)}, measurements={total_measurements}"
        )
        
        return Response({
            'resolution': resolution,
            'hexagons': results,
            'total_hexagons': len(results),
            'total_measurements': total_measurements,
            'statistics': statistics
        })

    @action(detail=False, methods=['get'], url_path='h3-aggregation-vertex')
    def h3_aggregation_vertex(self, request):
        """
        Aggregate light pollution measurements by H3 hexagons with vertex coordinates.

        Same as h3_aggregation but includes hexagon boundary vertices for rendering.
        """
        response = self.h3_aggregation(request)

        if response.status_code != 200:
            return response

        data = response.data
        hexagons = data.get('hexagons', [])

        for hexagon in hexagons:
            h3_index = hexagon.get('h3_index')
            if not h3_index:
                hexagon['vertices'] = []
                continue

            try:
                vertices = h3.cell_to_boundary(h3_index)
                hexagon['vertices'] = [[lat, lng] for lat, lng in vertices]
            except Exception as e:
                logger.warning(f"Failed to get vertices for H3 cell {h3_index}: {e}")
                hexagon['vertices'] = []

        return Response(data)

    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Get the total count of light pollution measurements (public access)"
    )
    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones de contaminación lumínica
        """
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        return Response({'total': total})

    @swagger_auto_schema(
        tags=['Measurements - Light Pollution'],
        operation_description="Get paginated light pollution measurements with progress metadata (public access)",
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER, default=1),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Items per page (max 15000)", type=openapi.TYPE_INTEGER, default=1000),
        ]
    )
    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Endpoint para obtener mediciones de contaminación lumínica paginadas con metadata de progreso
        """
        page = request.GET.get('page', 1)
        page_size = request.GET.get('page_size', 1000)

        try:
            page = int(page)
            page_size = int(page_size)
            page_size = min(page_size, 15000)
        except ValueError:
            return Response({'error': 'Invalid page or page_size parameter'}, status=400)

        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()

        paginator = Paginator(queryset, page_size)

        if page > paginator.num_pages:
            return Response({
                'results': [],
                'count': total,
                'num_pages': paginator.num_pages,
                'current_page': page,
                'page_size': page_size,
                'has_next': False,
                'has_previous': page > 1,
                'loaded_so_far': total,
                'error': 'Page beyond available pages'
            }, status=200)

        page_obj = paginator.get_page(page)
        serializer = self.get_serializer(page_obj, many=True)

        items_in_current_page = len(serializer.data)
        loaded_so_far = ((page - 1) * page_size) + items_in_current_page

        return Response({
            'results': serializer.data,
            'count': total,
            'num_pages': paginator.num_pages,
            'current_page': page,
            'page_size': page_size,
            'items_in_page': items_in_current_page,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
            'loaded_so_far': loaded_so_far
        })

class TrackViewSet(viewsets.ModelViewSet):
    """
    ViewSet for GPS tracks.
    - GET: Public access for public projects, authenticated for user's own tracks
    - POST: Requires authentication (upload CSV/GPX files), auto-assigns to current user
    - PUT/PATCH/DELETE: Only creator can modify/delete their tracks
    """
    serializer_class = TrackSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]  # Accept JSON and file uploads

    def get_permissions(self):
        """
        Requires authentication for all actions.
        """
        return [IsAuthenticated()]

    def get_queryset(self):
        """
        Filter tracks to show only the authenticated user's tracks.
        """
        if self.request.user.is_authenticated:
            return Track.objects.filter(
                created_by=self.request.user
            ).select_related('project', 'device', 'campaign', 'created_by')
        return Track.objects.none()

    def perform_create(self, serializer):
        """
        Auto-assign the current user when creating a track.
        """
        print(f"DEBUG - Request data: {self.request.data}")
        print(f"DEBUG - Serializer validated data: {serializer.validated_data}")
        track = serializer.save(created_by=self.request.user)
        print(f"DEBUG - Track saved: mission={track.mission}, campaign={track.campaign}")
        return track
    
    def perform_update(self, serializer):
        """
        Update track and validate hierarchy consistency.
        
        When project, mission, or campaign are updated, all linked measurements
        are automatically updated via the Track.save() method.
        """
        instance = serializer.save()
        # Validation happens in model's clean() method
        instance.full_clean()
        instance.save()
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="List all GPS tracks (public projects visible to all, private projects only to owners)"
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Create a new GPS track (requires authentication)",
        security=[{'Token': []}],
        responses={
            201: TrackSerializer,
            400: 'Invalid data',
            401: 'Not authenticated'
        }
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Get details of a specific GPS track"
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Update a GPS track (requires authentication and ownership)",
        security=[{'Token': []}],
        responses={
            200: TrackSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Track not found or not owned by user'
        }
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Partially update a GPS track (requires authentication and ownership)",
        security=[{'Token': []}],
        responses={
            200: TrackSerializer,
            400: 'Invalid data',
            401: 'Not authenticated',
            404: 'Track not found or not owned by user'
        }
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Delete a GPS track (requires authentication and ownership)",
        security=[{'Token': []}],
        responses={
            204: 'Track deleted successfully',
            401: 'Not authenticated',
            404: 'Track not found or not owned by user'
        }
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Get all tracks owned by the authenticated user",
        security=[{'Token': []}],
        manual_parameters=[
            openapi.Parameter(
                'project',
                openapi.IN_QUERY,
                description="Filter by project ID",
                type=openapi.TYPE_INTEGER,
                required=False
            ),
            openapi.Parameter(
                'mission',
                openapi.IN_QUERY,
                description="Filter by mission ID",
                type=openapi.TYPE_INTEGER,
                required=False
            ),
            openapi.Parameter(
                'campaign',
                openapi.IN_QUERY,
                description="Filter by campaign ID",
                type=openapi.TYPE_INTEGER,
                required=False
            ),
            openapi.Parameter(
                'status',
                openapi.IN_QUERY,
                description="Filter by processing status (pending/processing/completed/failed)",
                type=openapi.TYPE_STRING,
                required=False
            ),
        ],
        responses={
            200: TrackSerializer(many=True),
            401: 'Not authenticated'
        }
    )
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def my_tracks(self, request):
        """
        Get all tracks created by the authenticated user.
        
        This endpoint returns only the tracks that belong to the current user.
        Supports filtering by project, mission, campaign, and status.
        
        Query Parameters:
            - project (int): Filter by project ID
            - mission (int): Filter by mission ID
            - campaign (int): Filter by campaign ID
            - status (str): Filter by processing status
            
        Returns:
            List of tracks owned by the user with their details
        """
        # Base queryset: only user's tracks
        queryset = Track.objects.filter(created_by=request.user).select_related(
            'project', 'device', 'mission', 'campaign', 'created_by'
        ).order_by('-created_at')
        
        # Apply filters from query parameters
        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        status_filter = request.query_params.get('status')
        
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if mission_id:
            queryset = queryset.filter(mission_id=mission_id)
        if campaign_id:
            queryset = queryset.filter(campaign_id=campaign_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Paginate results
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Upload a track file (RCTRK format) and automatically create measurements",
        security=[{'Token': []}],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'file': openapi.Schema(
                    type=openapi.TYPE_FILE,
                    description='Track file (RCTRK format only)'
                ),
                'device': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Device ID'
                ),
                'project': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Project ID'
                ),
                'mission': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Mission ID (optional)'
                ),
                'campaign': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Campaign ID (optional)'
                ),
            },
            required=['file', 'device', 'project']
        ),
        responses={
            202: openapi.Response(
                description='Track upload accepted and queued for processing',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'track_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'job_id': openapi.Schema(type=openapi.TYPE_STRING),
                        'status': openapi.Schema(type=openapi.TYPE_STRING),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'status_url': openapi.Schema(type=openapi.TYPE_STRING)
                    }
                )
            ),
            400: 'Invalid file or data',
            401: 'Not authenticated'
        }
    )
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def upload(self, request):
        """
        Upload a track file - processing happens asynchronously with RQ
        
        Only RCTRK format (RadiaCode) is supported:
           Track: <name>\t<device>\t \tEC
           Timestamp\tTime\tLatitude\tLongitude\tAccuracy\tDoseRate\tCountRate\tComment
           134007603713620000\t2025-08-27 09:26:11\t41.2184571\t-1.1548868\t1.94\t6.52\t7.47\t 
        """
        file = request.FILES.get('file')
        device_id = request.data.get('device')
        project_id = request.data.get('project')
        mission_id = request.data.get('mission')
        campaign_id = request.data.get('campaign')
        
        if not file:
            return Response(
                {'error': 'No file provided'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        max_bytes = getattr(settings, 'TRACK_UPLOAD_MAX_BYTES', 10 * 1024 * 1024)
        if getattr(file, 'size', None) and file.size > max_bytes:
            return Response(
                {'error': f'File too large. Maximum is {max_bytes} bytes.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        
        if not device_id:
            return Response(
                {'error': 'Device ID is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not project_id:
            return Response(
                {'error': 'Project ID is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify device exists
        try:
            device = Device.objects.get(id=device_id)
        except Device.DoesNotExist:
            return Response(
                {'error': f'Device with id {device_id} not found'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify project exists
        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            return Response(
                {'error': f'Project with id {project_id} not found'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Detect file type
        file_extension = file.name.split('.')[-1].lower()
        if file_extension != 'rctrk':
            return Response(
                {'error': f'Unsupported file type: {file_extension}. Only RCTRK files are supported.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create Track object with status='pending'
        track = Track.objects.create(
            created_by=request.user,
            device=device,
            project=project,
            mission_id=mission_id if mission_id else None,
            campaign_id=campaign_id if campaign_id else None,
            file=file,
            file_type=file_extension,
            status='pending'  # ✅ Starts as pending
        )
        
        # ✅ Enqueue processing task with RQ
        queue = django_rq.get_queue('openred-tracks')
        job = queue.enqueue(
            'measures.tasks.process_track_file',
            track.id,
            job_timeout='10m',  # 10 minutes timeout
            result_ttl=86400,   # Keep result for 24 hours
            job_id=f'track_{track.id}'  # Unique job ID
        )
        
        return Response({
            'track_id': track.id,
            'job_id': job.id,
            'status': 'pending',
            'message': 'Track file uploaded successfully. Processing in background.'
        }, status=status.HTTP_202_ACCEPTED)  # ✅ 202 Accepted
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Get the processing status of a track",
        responses={
            200: openapi.Response(
                description='Track status',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'track_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'status': openapi.Schema(type=openapi.TYPE_STRING),
                        'measurements_count': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'error_message': openapi.Schema(type=openapi.TYPE_STRING),
                        'start_time': openapi.Schema(type=openapi.TYPE_STRING),
                        'end_time': openapi.Schema(type=openapi.TYPE_STRING),
                    }
                )
            )
        }
    )
    @action(detail=True, methods=['get'])
    def status(self, request, pk=None):
        """
        Get the processing status of a track
        """
        track = self.get_object()
        
        return Response({
            'track_id': track.id,
            'status': track.status,
            'measurements_count': track.total_measurements,
            'error_message': track.error_message if track.status == 'failed' else None,
            'start_time': track.start_time,
            'end_time': track.end_time,
        })
    
    @swagger_auto_schema(
        tags=['Tracks'],
        operation_description="Upload track data as JSON (RadiaCode mobile app format)",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['device', 'startedAt', 'points', 'project', 'mission', 'campaign'],
            properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING, description='Track name'),
                'description': openapi.Schema(type=openapi.TYPE_STRING, description='Track description'),
                'synced': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Synced status'),
                'syncedAt': openapi.Schema(type=openapi.TYPE_STRING, format='date-time', description='When synced'),
                'cloudTrackId': openapi.Schema(type=openapi.TYPE_STRING, description='Cloud track identifier'),
                'device': openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    required=['id'],
                    properties={
                        'name': openapi.Schema(type=openapi.TYPE_STRING, description='Device name'),
                        'id': openapi.Schema(type=openapi.TYPE_STRING, description='Device serial/MAC address'),
                    }
                ),
                'startedAt': openapi.Schema(type=openapi.TYPE_STRING, format='date-time', description='Track start time'),
                'endedAt': openapi.Schema(type=openapi.TYPE_STRING, format='date-time', description='Track end time'),
                'requiredGpsAccuracyMeters': openapi.Schema(type=openapi.TYPE_NUMBER, description='Required GPS accuracy'),
                'points': openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        required=['timestamp', 'latitude', 'longitude'],
                        properties={
                            'timestamp': openapi.Schema(type=openapi.TYPE_STRING, format='date-time'),
                            'latitude': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'longitude': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'altitude': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'speed': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'accuracyMeters': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'cpm': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'doseMicroSvPerHour': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'lux': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'cct': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'cieX': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'cieY': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'cieU': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'cieV': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'duv': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'tint': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'mode': openapi.Schema(type=openapi.TYPE_INTEGER),
                            'channels': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_NUMBER)),
                            'temperature': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'batteryMv': openapi.Schema(type=openapi.TYPE_INTEGER),
                        }
                    )
                ),
                'trackType': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Track type (defaults to radiation if omitted)',
                    enum=['radiation', 'light'],
                ),
                'project': openapi.Schema(type=openapi.TYPE_INTEGER, description='Project ID (required)'),
                'mission': openapi.Schema(type=openapi.TYPE_INTEGER, description='Mission ID (required)'),
                'campaign': openapi.Schema(type=openapi.TYPE_INTEGER, description='Campaign ID (required)'),
                'campaign_password': openapi.Schema(type=openapi.TYPE_STRING, description='Campaign password (required only if campaign is protected)'),
            }
        ),
        responses={
            202: openapi.Response(
                description='Track processing enqueued',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'track_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'job_id': openapi.Schema(type=openapi.TYPE_STRING),
                        'status': openapi.Schema(type=openapi.TYPE_STRING),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                    }
                )
            ),
            400: 'Invalid data',
            401: 'Not authenticated'
        }
    )
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def upload_json(self, request):
        """
        Upload track data as JSON from RadiaCode mobile app.
        
        Accepts JSON with device info, track metadata, and measurement points.
        Processing happens asynchronously with RQ.
        """
        import json
        import tempfile
        from django.core.files.base import ContentFile
        
        data = request.data

        max_bytes = getattr(settings, 'TRACK_UPLOAD_MAX_BYTES', 10 * 1024 * 1024)
        content_length = request.META.get('CONTENT_LENGTH')
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    return Response(
                        {'error': f'Request too large. Maximum is {max_bytes} bytes.'},
                        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except (TypeError, ValueError):
                pass
        
        # Validate required fields
        if not data.get('device') or not data.get('device', {}).get('id'):
            return Response(
                {'error': 'Device ID is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        mission_id = data.get('mission')
        campaign_id = data.get('campaign')
        project_id = data.get('project')

        if not project_id:
            return Response({'error': 'Project ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not mission_id:
            return Response({'error': 'Mission ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not campaign_id:
            return Response({'error': 'Campaign ID is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        points = data.get('points', [])
        if not points:
            return Response(
                {'error': 'At least one measurement point is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        max_points = getattr(settings, 'TRACK_UPLOAD_MAX_POINTS', 20000)
        if len(points) > max_points:
            return Response(
                {'error': f'Too many points. Maximum is {max_points}.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        
        # Get or create device by serial number (MAC address)
        device_serial = data['device']['id']
        device_name = data['device'].get('name', 'RadiaCode Device')
        
        try:
            device = Device.objects.get(serial_number=device_serial)
        except Device.DoesNotExist:
            # Auto-create device if not exists
            # Try to find or create a default RadiaCode device model
            from devices.models import DeviceModel
            
            device_model, _ = DeviceModel.objects.get_or_create(
                name="RadiaCode",
                defaults={
                    'manufacturer': 'Scan Electronics',
                    'technology': 'Geiger-Müller tube',
                    'max_radiation_range': 1000.0,
                    'validatedByOpenRed': False,
                    'description': 'RadiaCode radiation detector (auto-registered from mobile app)'
                }
            )
            
            # Create the device associated to the current user
            device = Device.objects.create(
                device_model=device_model,
                serial_number=device_serial,
                owner=request.user,
                is_active=True
            )
            
            logger.info(f"Auto-created device {device_serial} for user {request.user.username}")
        
        # Validate mission/campaign and derive project
        from missions.models import Mission, Campaign

        try:
            mission = Mission.objects.get(id=mission_id)
        except Mission.DoesNotExist:
            return Response({'error': f'Mission with id {mission_id} not found'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            campaign = Campaign.objects.get(id=campaign_id)
        except Campaign.DoesNotExist:
            return Response({'error': f'Campaign with id {campaign_id} not found'}, status=status.HTTP_400_BAD_REQUEST)

        if campaign.mission_id != mission.id:
            return Response(
                {'error': 'Campaign does not belong to the provided mission'},
                status=status.HTTP_400_BAD_REQUEST
            )

        project = mission.project
        if project_id and str(project.id) != str(project_id):
            return Response(
                {'error': 'Project does not match mission.project'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate campaign password if campaign is protected
        if campaign.password:
            campaign_password = data.get('campaign_password')

            if not campaign_password:
                return Response(
                    {'error': 'Esta campaña requiere contraseña para subir tracks.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if campaign_password != campaign.password:
                return Response(
                    {'error': 'Contraseña de campaña incorrecta.'},
                    status=status.HTTP_403_FORBIDDEN
                )
        
        # Save JSON data as a file
        # Use the name from JSON, fallback to timestamp if not provided
        track_name = data.get('name', f"track_{timezone.now().strftime('%Y%m%d_%H%M%S')}")
        # Sanitize filename (remove invalid characters)
        import re
        safe_name = re.sub(r'[^\w\s-]', '', track_name).strip().replace(' ', '_')
        json_filename = safe_name if safe_name else f"track_{timezone.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Store compact JSON to reduce disk usage
        json_content = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        json_bytes = json_content.encode('utf-8')
        if len(json_bytes) > max_bytes:
            return Response(
                {'error': f'Payload too large after encoding. Maximum is {max_bytes} bytes.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        json_file = ContentFile(json_bytes, name=json_filename)
        
        # Create Track object with status='pending' and validate model integrity
        track = Track(
            created_by=request.user,
            device=device,
            project=project,
            mission=mission,
            campaign=campaign,
            file=json_file,
            file_type='json',
            description=data.get('description', ''),
            status='pending',
            # Additional JSON metadata
            synced=data.get('synced', False),
            synced_at=parse_datetime(data['syncedAt']) if data.get('syncedAt') else None,
            cloud_track_id=data.get('cloudTrackId'),
            required_gps_accuracy_meters=data.get('requiredGpsAccuracyMeters'),
        )

        track.full_clean()
        track.save()
        
        # Enqueue processing task with RQ
        queue = django_rq.get_queue('openred-tracks')
        job = queue.enqueue(
            'measures.tasks.process_track_file',
            track.id,
            job_timeout='10m',
            result_ttl=86400,
            job_id=f'track_{track.id}'
        )
        
        return Response({
            'id': track.id,
            'job_id': job.id,
            'status': 'pending',
            'message': 'Track data uploaded successfully. Processing in background.'
        }, status=status.HTTP_202_ACCEPTED)

# Para compatibilidad temporal con el frontend existente
# El frontend sigue llamando a /measurements/, por lo que mantenemos este ViewSet
class MeasurementViewSet(viewsets.ModelViewSet):
    # Por defecto, usa RadiationMeasurement para compatibilidad
    queryset = RadiationMeasurement.objects.all()
    serializer_class = MeasurementSerializer

    def get_queryset(self):
        """
        Filtrar mediciones por proyecto si se especifica el parámetro.
        Retorna el queryset apropiado según el tipo de proyecto.
        """
        project_id = self.request.query_params.get('project', None)
        
        if project_id:
            try:
                project_id = int(project_id)
                project = Project.objects.get(id=project_id)
                
                # Retornar queryset según el tipo de proyecto
                if project.project_type == 'radiation':
                    return RadiationMeasurement.objects.filter(project_id=project_id)
                elif project.project_type == 'light_pollution':
                    return LightPollutionMeasurement.objects.filter(project_id=project_id)
                
            except (ValueError, TypeError, Project.DoesNotExist):
                pass  # Ignorar valores inválidos, usar queryset por defecto
        
        # Por defecto, retornar RadiationMeasurement para compatibilidad
        return super().get_queryset()

    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones.
        Cuenta mediciones del tipo correcto según el proyecto especificado.
        """
        project_id = self.request.query_params.get('project', None)
        
        if project_id:
            try:
                project_id = int(project_id)
                project = Project.objects.get(id=project_id)
                
                # Contar según el tipo de proyecto
                if project.project_type == 'radiation':
                    total = RadiationMeasurement.objects.filter(project_id=project_id).count()
                elif project.project_type == 'light_pollution':
                    total = LightPollutionMeasurement.objects.filter(project_id=project_id).count()
                else:
                    total = 0
                
                return Response({
                    'total': total,
                    'project_id': project_id,
                    'project_type': project.project_type,
                    'project_name': project.name
                })
                
            except (ValueError, TypeError):
                return Response({'error': 'Invalid project_id parameter'}, status=400)
            except Project.DoesNotExist:
                return Response({'error': f'Project with id {project_id} does not exist'}, status=404)
        
        # Si no se especifica proyecto, contar solo RadiationMeasurement por compatibilidad
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        return Response({'total': total, 'note': 'No project specified, counting radiation measurements only'})

    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Endpoint para obtener mediciones paginadas con metadata de progreso.
        Retorna mediciones del tipo correcto según el proyecto especificado.
        """
        page = request.GET.get('page', 1)
        page_size = request.GET.get('page_size', 1000)
        project_id = request.GET.get('project', None)

        try:
            page = int(page)
            page_size = int(page_size)
            # Aumentar el límite máximo para permitir chunks más grandes
            page_size = min(page_size, 15000)  # Límite máximo más alto
        except ValueError:
            return Response({'error': 'Invalid page or page_size parameter'}, status=400)

        # Determinar el queryset correcto según el proyecto
        if project_id:
            try:
                project_id = int(project_id)
                project = Project.objects.get(id=project_id)
                
                if project.project_type == 'radiation':
                    queryset = RadiationMeasurement.objects.filter(project_id=project_id)
                    serializer_class = RadiationMeasurementSerializer
                elif project.project_type == 'light_pollution':
                    queryset = LightPollutionMeasurement.objects.filter(project_id=project_id)
                    serializer_class = LightPollutionMeasurementSerializer
                else:
                    return Response({'error': f'Unsupported project type: {project.project_type}'}, status=400)
                
            except (ValueError, TypeError):
                return Response({'error': 'Invalid project_id parameter'}, status=400)
            except Project.DoesNotExist:
                return Response({'error': f'Project with id {project_id} does not exist'}, status=404)
        else:
            # Sin proyecto especificado, usar comportamiento por defecto
            queryset = self.filter_queryset(self.get_queryset())
            serializer_class = self.get_serializer_class()

        total = queryset.count()
        paginator = Paginator(queryset, page_size)

        if page > paginator.num_pages:
            return Response({
                'results': [],
                'count': total,
                'num_pages': paginator.num_pages,
                'current_page': page,
                'page_size': page_size,
                'has_next': False,
                'has_previous': page > 1,
                'loaded_so_far': total,
                'project_id': project_id if project_id else None,
                'error': 'Page beyond available pages'
            }, status=200)  # Cambiar a 200 para mejor manejo

        page_obj = paginator.get_page(page)
        serializer = serializer_class(page_obj, many=True)

        # Calcular correctamente los elementos cargados hasta ahora
        items_in_current_page = len(serializer.data)
        loaded_so_far = ((page - 1) * page_size) + items_in_current_page

        return Response({
            'results': serializer.data,
            'count': total,
            'num_pages': paginator.num_pages,
            'current_page': page,
            'page_size': page_size,
            'items_in_page': items_in_current_page,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
            'loaded_so_far': loaded_so_far,
            'project_id': project_id if project_id else None
        })