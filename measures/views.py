"""
Views for the measures app.

This module provides API ViewSets for measurement data and track file uploads.
Includes permission handling for public read access and authenticated write operations.

ViewSets:
    - RadiationMeasurementViewSet: CRUD for gamma radiation measurements with H3 aggregation
    - LightPollutionMeasurementViewSet: CRUD for light pollution measurements with H3 aggregation
    - TrackViewSet: Upload and manage measurement track files (CSV/GPX)
"""
from django.shortcuts import render, get_object_or_404
from rest_framework import viewsets, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAuthenticated, AllowAny
from rest_framework.throttling import ScopedRateThrottle
from django.contrib.gis.geos import Point, Polygon
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
from django.utils import translation
from django.utils.translation import gettext
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, BasePermission, SAFE_METHODS
from .scoping import scope_capture_mode_qs, scope_capture_mode_sql
from .timeseries import (
    RAW_READINGS_CAP,
    bucketed_readings,
    bucketed_weather,
    interval_label,
    parse_time_window,
    resolve_interval,
)
from django.core.paginator import Paginator
from django.db import connection, transaction
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import RadiationMeasurement, LightPollutionMeasurement, Track, Spectrum, Station
from missions.models import Project
from devices.models import Device
from .serializers import (
    RadiationMeasurementSerializer,
    LightPollutionMeasurementSerializer,
    TrackSerializer,
    SpectrumSerializer,
    StationSerializer,
    MeasurementSerializer  # Backward compatibility alias
)

logger = logging.getLogger(__name__)


# ── Report language ───────────────────────────────────────────────────────
#: strftime pattern for the "generated on" stamp of PDF reports, per language.
REPORT_DATETIME_FORMATS = {
    'en': '%Y-%m-%d %H:%M',
    'es': '%d/%m/%Y %H:%M',
}


def report_language(request):
    """
    Pick the language for a generated PDF report.

    Precedence: explicit ``lang`` query parameter → Accept-Language header →
    ``settings.REPORT_DEFAULT_LANGUAGE``. Only languages listed in
    ``settings.LANGUAGES`` are honoured; anything else falls back to the default.
    """
    supported = {code for code, _name in settings.LANGUAGES}
    default = settings.REPORT_DEFAULT_LANGUAGE
    lang = (request.query_params.get('lang') or '').strip().lower()
    if lang:
        lang = lang.split('-')[0]
        return lang if lang in supported else default
    header = request.META.get('HTTP_ACCEPT_LANGUAGE')
    if header:
        try:
            lang = translation.get_language_from_request(request)
        except Exception:
            lang = None
        if lang:
            lang = lang.split('-')[0]
            if lang in supported:
                return lang
    return default


def report_generated_at(now, lang):
    return now.strftime(REPORT_DATETIME_FORMATS.get(lang, REPORT_DATETIME_FORMATS['en']))


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
        if self.action in ['list', 'retrieve', 'h3_aggregation', 'h3_aggregation_vertex', 'count', 'paginated', 'report', 'histogram']:
            return [AllowAny()]
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

    def filter_queryset(self, queryset):
        """
        Apply query-param filters (project, campaign, mission, track, device,
        date range, bounding box, altitude/dose_rate/speed ranges).

        Centralizing them here means list(), count() and paginated() filter
        identically — all three call self.filter_queryset().
        """
        from django.utils.dateparse import parse_date
        request = self.request

        track_id = request.query_params.get('track')
        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        device_id = request.query_params.get('device')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east = request.query_params.get('east')
        west = request.query_params.get('west')

        min_altitude = request.query_params.get('min_altitude')
        max_altitude = request.query_params.get('max_altitude')
        min_dose_rate = request.query_params.get('min_dose_rate')
        max_dose_rate = request.query_params.get('max_dose_rate')
        min_speed = request.query_params.get('min_speed')
        max_speed = request.query_params.get('max_speed')

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

        if start_date:
            parsed_date = parse_date(start_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__gte=timezone.make_aware(datetime.combine(parsed_date, datetime.min.time())))
        if end_date:
            parsed_date = parse_date(end_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__lte=timezone.make_aware(datetime.combine(parsed_date, datetime.max.time())))

        if north and south and east and west:
            try:
                bbox = Polygon.from_bbox((float(west), float(south), float(east), float(north)))
                bbox.srid = 4326
                queryset = queryset.filter(location__within=bbox)
            except ValueError:
                pass
        else:
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

        if min_altitude:
            try:
                queryset = queryset.filter(altitude__gte=float(min_altitude))
            except ValueError:
                pass
        if max_altitude:
            try:
                queryset = queryset.filter(altitude__lte=float(max_altitude))
            except ValueError:
                pass

        if min_dose_rate:
            try:
                queryset = queryset.filter(dose_rate__gte=float(min_dose_rate))
            except ValueError:
                pass
        if max_dose_rate:
            try:
                queryset = queryset.filter(dose_rate__lte=float(max_dose_rate))
            except ValueError:
                pass

        if min_speed:
            try:
                queryset = queryset.filter(speed__gte=float(min_speed))
            except ValueError:
                pass
        if max_speed:
            try:
                queryset = queryset.filter(speed__lte=float(max_speed))
            except ValueError:
                pass

        # list() and paginated() feed the map, so they are movement-only by
        # default; count() reports the project's real data volume and counts
        # every mode unless asked otherwise.
        return scope_capture_mode_qs(
            request, queryset,
            default=None if self.action == 'count' else 'movement',
        )

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
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter measurements from this date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter measurements up to this date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box: maximum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box: minimum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box: maximum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box: minimum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_altitude', openapi.IN_QUERY, description="Minimum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_altitude', openapi.IN_QUERY, description="Maximum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_dose_rate', openapi.IN_QUERY, description="Minimum dose rate (µSv/h)", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_dose_rate', openapi.IN_QUERY, description="Maximum dose rate (µSv/h)", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_speed', openapi.IN_QUERY, description="Minimum speed in m/s", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_speed', openapi.IN_QUERY, description="Maximum speed in m/s", type=openapi.TYPE_NUMBER),
        ]
    )
    def list(self, request, *args, **kwargs):
        import time
        start_time = time.time()
        start_queries = len(connection.queries)

        queryset = self.filter_queryset(self.get_queryset())

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
                   f"Filters: track={request.query_params.get('track')}, "
                   f"project={request.query_params.get('project')}, "
                   f"device={request.query_params.get('device')}")
        
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
        # --- TEMPORALMENTE DESHABILITADO (crear medida suelta) ---
        # No valida la contraseña de la campaña; la única vía de subida es /api/tracks/upload_json/.
        # Para reactivar: eliminar este bloque return.
        return Response(
            {'error': 'La creación de medidas sueltas está temporalmente deshabilitada. '
                      'Sube los datos como track JSON: /api/tracks/upload_json/'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
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
            openapi.Parameter('mission', openapi.IN_QUERY, description="Filter by mission ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('min_count', openapi.IN_QUERY, description="Minimum measurements per hexagon (default: 1)", type=openapi.TYPE_INTEGER, default=1),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter measurements from this date (format: YYYY-MM-DD, example: 2024-01-15)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter measurements until this date (format: YYYY-MM-DD, example: 2024-12-31)", type=openapi.TYPE_STRING),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box: maximum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box: minimum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box: maximum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box: minimum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_altitude', openapi.IN_QUERY, description="Minimum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_altitude', openapi.IN_QUERY, description="Maximum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_dose_rate', openapi.IN_QUERY, description="Minimum dose rate (µSv/h)", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_dose_rate', openapi.IN_QUERY, description="Maximum dose rate (µSv/h)", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_speed', openapi.IN_QUERY, description="Minimum speed in m/s", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_speed', openapi.IN_QUERY, description="Maximum speed in m/s", type=openapi.TYPE_NUMBER),
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

        # Range filters
        min_altitude = request.query_params.get('min_altitude')
        max_altitude = request.query_params.get('max_altitude')
        min_dose_rate = request.query_params.get('min_dose_rate')
        max_dose_rate = request.query_params.get('max_dose_rate')
        min_speed = request.query_params.get('min_speed')
        max_speed = request.query_params.get('max_speed')

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

        # Bounding box filters — ST_Within con índice espacial GIST cuando vienen los 4 params
        if north is not None and south is not None and east is not None and west is not None:
            try:
                north_val, south_val = float(north), float(south)
                east_val, west_val = float(east), float(west)
                where_clauses.append(
                    "ST_Within(location::geometry, ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326))"
                )
                params.update({'north': north_val, 'south': south_val, 'east': east_val, 'west': west_val})
            except ValueError:
                return Response({'error': 'Invalid bounding box parameters.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            if north is not None:
                try:
                    where_clauses.append("latitude <= %(north)s")
                    params['north'] = float(north)
                except ValueError:
                    return Response({'error': 'Invalid north parameter.'}, status=status.HTTP_400_BAD_REQUEST)
            if south is not None:
                try:
                    where_clauses.append("latitude >= %(south)s")
                    params['south'] = float(south)
                except ValueError:
                    return Response({'error': 'Invalid south parameter.'}, status=status.HTTP_400_BAD_REQUEST)
            if east is not None:
                try:
                    where_clauses.append("longitude <= %(east)s")
                    params['east'] = float(east)
                except ValueError:
                    return Response({'error': 'Invalid east parameter.'}, status=status.HTTP_400_BAD_REQUEST)
            if west is not None:
                try:
                    where_clauses.append("longitude >= %(west)s")
                    params['west'] = float(west)
                except ValueError:
                    return Response({'error': 'Invalid west parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        # Date filters (format: YYYY-MM-DD)
        if start_date:
            try:
                from django.utils.dateparse import parse_date
                parsed_date = parse_date(start_date)
                if parsed_date:
                    parsed_date = timezone.make_aware(datetime.combine(parsed_date, datetime.min.time()))
                    where_clauses.append('"dateTime" >= %(start_date)s')
                    params['start_date'] = parsed_date
                else:
                    return Response(
                        {'error': 'Invalid start_date format. Use format: YYYY-MM-DD (example: 2024-01-15)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                return Response({'error': f'Invalid start_date: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

        if end_date:
            try:
                from django.utils.dateparse import parse_date
                parsed_date = parse_date(end_date)
                if parsed_date:
                    parsed_date = timezone.make_aware(datetime.combine(parsed_date, datetime.max.time()))
                    where_clauses.append('"dateTime" <= %(end_date)s')
                    params['end_date'] = parsed_date
                else:
                    return Response(
                        {'error': 'Invalid end_date format. Use format: YYYY-MM-DD (example: 2024-12-31)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                return Response({'error': f'Invalid end_date: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

        # Altitude range filters
        if min_altitude is not None:
            try:
                where_clauses.append("altitude >= %(min_altitude)s")
                params['min_altitude'] = float(min_altitude)
            except ValueError:
                return Response({'error': 'Invalid min_altitude parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        if max_altitude is not None:
            try:
                where_clauses.append("altitude <= %(max_altitude)s")
                params['max_altitude'] = float(max_altitude)
            except ValueError:
                return Response({'error': 'Invalid max_altitude parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        # Dose rate range filters
        if min_dose_rate is not None:
            try:
                where_clauses.append("dose_rate >= %(min_dose_rate)s")
                params['min_dose_rate'] = float(min_dose_rate)
            except ValueError:
                return Response({'error': 'Invalid min_dose_rate parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        if max_dose_rate is not None:
            try:
                where_clauses.append("dose_rate <= %(max_dose_rate)s")
                params['max_dose_rate'] = float(max_dose_rate)
            except ValueError:
                return Response({'error': 'Invalid max_dose_rate parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        # Speed range filters
        if min_speed is not None:
            try:
                where_clauses.append("speed >= %(min_speed)s")
                params['min_speed'] = float(min_speed)
            except ValueError:
                return Response({'error': 'Invalid min_speed parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        if max_speed is not None:
            try:
                where_clauses.append("speed <= %(max_speed)s")
                params['max_speed'] = float(max_speed)
            except ValueError:
                return Response({'error': 'Invalid max_speed parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        scope_capture_mode_sql(request, where_clauses, params)

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
        operation_description=(
            "Get dose_rate histogram for radiation measurements using fixed bins: "
            "[0-0.08, 0.08-0.12, 0.12-0.16, 0.16-0.20, 0.20-0.30, 0.30-0.50, >0.50]"
        ),
        manual_parameters=[
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('mission', openapi.IN_QUERY, description="Filter by mission ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box north", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box south", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box east", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box west", type=openapi.TYPE_NUMBER),
        ]
    )
    @action(detail=False, methods=['get'], url_path='histogram')
    def histogram(self, request):
        where_clauses = ["latitude IS NOT NULL", "longitude IS NOT NULL", "dose_rate IS NOT NULL"]
        params = {}

        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        track_id = request.query_params.get('track')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east = request.query_params.get('east')
        west = request.query_params.get('west')

        if project_id:
            where_clauses.append("project_id = %(project_id)s")
            params['project_id'] = project_id
        if mission_id:
            where_clauses.append("campaign_id IN (SELECT id FROM missions_campaign WHERE mission_id = %(mission_id)s)")
            params['mission_id'] = mission_id
        if campaign_id:
            where_clauses.append("campaign_id = %(campaign_id)s")
            params['campaign_id'] = campaign_id
        if track_id:
            where_clauses.append("track_id = %(track_id)s")
            params['track_id'] = track_id
        if north and south and east and west:
            try:
                where_clauses.append("latitude <= %(north)s AND latitude >= %(south)s AND longitude <= %(east)s AND longitude >= %(west)s")
                params.update({'north': float(north), 'south': float(south), 'east': float(east), 'west': float(west)})
            except ValueError:
                return Response({'error': 'Invalid bounding box parameters'}, status=status.HTTP_400_BAD_REQUEST)
        if start_date:
            try:
                from django.utils.dateparse import parse_date
                parsed = parse_date(start_date)
                if parsed:
                    parsed = timezone.make_aware(datetime.combine(parsed, datetime.min.time()))
                    where_clauses.append('"dateTime" >= %(start_date)s')
                    params['start_date'] = parsed
            except Exception as e:
                return Response({'error': f'Invalid start_date: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
        if end_date:
            try:
                from django.utils.dateparse import parse_date
                parsed = parse_date(end_date)
                if parsed:
                    parsed = timezone.make_aware(datetime.combine(parsed, datetime.max.time()))
                    where_clauses.append('"dateTime" <= %(end_date)s')
                    params['end_date'] = parsed
            except Exception as e:
                return Response({'error': f'Invalid end_date: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

        scope_capture_mode_sql(request, where_clauses, params)

        where_sql = " AND ".join(where_clauses)

        sql = f"""
        SELECT
            COUNT(*) FILTER (WHERE dose_rate < 0.08)::int                           AS bin_1,
            COUNT(*) FILTER (WHERE dose_rate >= 0.08 AND dose_rate < 0.12)::int     AS bin_2,
            COUNT(*) FILTER (WHERE dose_rate >= 0.12 AND dose_rate < 0.16)::int     AS bin_3,
            COUNT(*) FILTER (WHERE dose_rate >= 0.16 AND dose_rate < 0.20)::int     AS bin_4,
            COUNT(*) FILTER (WHERE dose_rate >= 0.20 AND dose_rate < 0.30)::int     AS bin_5,
            COUNT(*) FILTER (WHERE dose_rate >= 0.30 AND dose_rate < 0.50)::int     AS bin_6,
            COUNT(*) FILTER (WHERE dose_rate >= 0.50)::int                          AS bin_7,
            COUNT(*)::int                                                            AS total,
            MIN(dose_rate)::float                                                    AS min_val,
            MAX(dose_rate)::float                                                    AS max_val
        FROM measures_radiation_measurement
        WHERE {where_sql};
        """

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()

        if not row or row[7] == 0:
            return Response({
                'total': 0,
                'bins': [],
            })

        bins = [
            {'label': '0 – 0.08',  'min': 0.00, 'max': 0.08, 'count': row[0]},
            {'label': '0.08 – 0.12', 'min': 0.08, 'max': 0.12, 'count': row[1]},
            {'label': '0.12 – 0.16', 'min': 0.12, 'max': 0.16, 'count': row[2]},
            {'label': '0.16 – 0.20', 'min': 0.16, 'max': 0.20, 'count': row[3]},
            {'label': '0.20 – 0.30', 'min': 0.20, 'max': 0.30, 'count': row[4]},
            {'label': '0.30 – 0.50', 'min': 0.30, 'max': 0.50, 'count': row[5]},
            {'label': '> 0.50',    'min': 0.50, 'max': None,  'count': row[6]},
        ]

        return Response({
            'total': row[7],
            'min': round(row[8], 6),
            'max': round(row[9], 6),
            'bins': bins,
        })

    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Get the total count of radiation measurements (public access)"
    )
    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones de radiación.

        `total` es el volumen real de datos: incluye las medidas de estación.
        El desglose por modo de captura permite a quien pinta el mapa saber
        cuántas medidas va a recibir realmente (`movement`), ya que las capas
        espaciales excluyen las estaciones.
        """
        queryset = self.filter_queryset(self.get_queryset())
        counts = queryset.aggregate(
            total=Count('id'),
            movement=Count('id', filter=Q(capture_mode='movement')),
            static=Count('id', filter=Q(capture_mode='static')),
        )
        return Response({
            'total': counts['total'],
            'movement': counts['movement'],
            'static': counts['static'],
        })

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

    @staticmethod
    def _build_rad_where(request):
        """
        Build the WHERE clause and params dict shared by h3_aggregation and report.
        Returns (where_clauses, params, bbox_ctx, error_response).
        """
        from django.utils.dateparse import parse_date

        where_clauses = ["latitude IS NOT NULL", "longitude IS NOT NULL", "dose_rate IS NOT NULL"]
        params = {}

        project_id  = request.query_params.get('project')
        mission_id  = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        track_id    = request.query_params.get('track')
        start_date  = request.query_params.get('start_date')
        end_date    = request.query_params.get('end_date')
        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east  = request.query_params.get('east')
        west  = request.query_params.get('west')
        min_dose_rate = request.query_params.get('min_dose_rate')
        max_dose_rate = request.query_params.get('max_dose_rate')
        min_altitude  = request.query_params.get('min_altitude')
        max_altitude  = request.query_params.get('max_altitude')
        min_speed     = request.query_params.get('min_speed')
        max_speed     = request.query_params.get('max_speed')

        if project_id:
            where_clauses.append("project_id = %(project_id)s")
            params['project_id'] = project_id

        if mission_id:
            where_clauses.append("campaign_id IN (SELECT id FROM missions_campaign WHERE mission_id = %(mission_id)s)")
            params['mission_id'] = mission_id

        if campaign_id:
            where_clauses.append("campaign_id = %(campaign_id)s")
            params['campaign_id'] = campaign_id

        if track_id:
            where_clauses.append("track_id = %(track_id)s")
            params['track_id'] = track_id

        bbox_ctx = None
        if north is not None and south is not None and east is not None and west is not None:
            try:
                n, s, e, w = float(north), float(south), float(east), float(west)
                where_clauses.append(
                    "ST_Within(location::geometry, ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326))"
                )
                params.update({'north': n, 'south': s, 'east': e, 'west': w})
                bbox_ctx = {'north': n, 'south': s, 'east': e, 'west': w}
            except ValueError:
                return where_clauses, params, None, Response(
                    {'error': 'Invalid bounding box parameters.'}, status=status.HTTP_400_BAD_REQUEST
                )
        else:
            for val, key, clause in [
                (north, 'north', "latitude <= %(north)s"),
                (south, 'south', "latitude >= %(south)s"),
                (east,  'east',  "longitude <= %(east)s"),
                (west,  'west',  "longitude >= %(west)s"),
            ]:
                if val is not None:
                    try:
                        params[key] = float(val)
                        where_clauses.append(clause)
                    except ValueError:
                        return where_clauses, params, None, Response(
                            {'error': f'Invalid {key} parameter.'}, status=status.HTTP_400_BAD_REQUEST
                        )

        if start_date:
            parsed = parse_date(start_date)
            if parsed:
                params['start_date'] = timezone.make_aware(datetime.combine(parsed, datetime.min.time()))
                where_clauses.append('"dateTime" >= %(start_date)s')
            else:
                return where_clauses, params, None, Response(
                    {'error': 'Invalid start_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST
                )

        if end_date:
            parsed = parse_date(end_date)
            if parsed:
                params['end_date'] = timezone.make_aware(datetime.combine(parsed, datetime.max.time()))
                where_clauses.append('"dateTime" <= %(end_date)s')
            else:
                return where_clauses, params, None, Response(
                    {'error': 'Invalid end_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST
                )

        for val, key, clause in [
            (min_dose_rate, 'min_dose_rate', "dose_rate >= %(min_dose_rate)s"),
            (max_dose_rate, 'max_dose_rate', "dose_rate <= %(max_dose_rate)s"),
            (min_altitude,  'min_altitude',  "(altitude IS NULL OR altitude >= %(min_altitude)s)"),
            (max_altitude,  'max_altitude',  "(altitude IS NULL OR altitude <= %(max_altitude)s)"),
            (min_speed,     'min_speed',     "(speed IS NULL OR speed >= %(min_speed)s)"),
            (max_speed,     'max_speed',     "(speed IS NULL OR speed <= %(max_speed)s)"),
        ]:
            if val is not None:
                try:
                    params[key] = float(val)
                    where_clauses.append(clause)
                except ValueError:
                    return where_clauses, params, None, Response(
                        {'error': f'Invalid {key} parameter.'}, status=status.HTTP_400_BAD_REQUEST
                    )

        scope_capture_mode_sql(request, where_clauses, params)

        return where_clauses, params, bbox_ctx, None

    @swagger_auto_schema(
        tags=['Measurements - Radiation'],
        operation_description="Generate a PDF report for the current selection. Accepts the same filters as h3-aggregation.",
        manual_parameters=[
            openapi.Parameter('lang', openapi.IN_QUERY, description="Report language: 'en' or 'es' (default: Accept-Language header, then REPORT_DEFAULT_LANGUAGE)", type=openapi.TYPE_STRING),
            openapi.Parameter('map_mode', openapi.IN_QUERY, description="Map style: 'auto' (default: points when the area spans ≤1.5°, hexagons otherwise), 'points' or 'hexagons'", type=openapi.TYPE_STRING),
            openapi.Parameter('resolution', openapi.IN_QUERY, description="H3 resolution for the hexagon map (0-15, default: auto from the map extent)", type=openapi.TYPE_INTEGER),
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('mission', openapi.IN_QUERY, description="Filter by mission ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box: max latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box: min latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box: max longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box: min longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_dose_rate', openapi.IN_QUERY, description="Minimum dose rate (μSv/h)", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_dose_rate', openapi.IN_QUERY, description="Maximum dose rate (μSv/h)", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_altitude', openapi.IN_QUERY, description="Minimum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_altitude', openapi.IN_QUERY, description="Maximum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_speed', openapi.IN_QUERY, description="Minimum speed in m/s", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_speed', openapi.IN_QUERY, description="Maximum speed in m/s", type=openapi.TYPE_NUMBER),
        ],
        responses={200: openapi.Response(description="PDF file", schema=openapi.Schema(type=openapi.TYPE_FILE))}
    )
    @action(detail=False, methods=['get'], url_path='report')
    def report(self, request):
        lang = report_language(request)
        with translation.override(lang):
            return self._render_report(request, lang)

    def _render_report(self, request, lang):
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        from weasyprint import HTML
        from .report_generators import (
            generate_h3_map, generate_dose_rate_histogram,
            generate_cpm_histogram, generate_dose_rate_cpm_scatter,
            generate_radiation_classification_chart, generate_altitude_dose_scatter,
            generate_radiation_time_series, generate_radiation_hour_distribution,
            bbox_to_resolution,
        )
        import datetime as dt

        # ── 1. Parse filters ──────────────────────────────────────────────
        where_clauses, params, bbox_ctx, err = self._build_rad_where(request)
        if err:
            return err

        from .report_generators import (
            generate_points_map, choose_map_mode, data_extent, MAP_POINTS_LIMIT,
        )
        try:
            explicit_resolution = max(0, min(15, int(request.query_params.get('resolution', 0))))
        except ValueError:
            explicit_resolution = 0
        map_mode_param = request.query_params.get('map_mode')

        params['min_count']    = 1
        params['points_limit'] = MAP_POINTS_LIMIT
        # Drop bogus GPS fixes at "null island" (0,0 ± noise): they stretch the map
        # to the Gulf of Guinea and no real measurement is taken there.
        where_clauses.append("NOT (abs(latitude) < 0.01 AND abs(longitude) < 0.01)")
        where_sql = " AND ".join(where_clauses)

        # ── 2. Run queries ────────────────────────────────────────────────
        with connection.cursor() as cursor:

            # Individual points for the map (random sample when over the limit).
            # Fetched first: without an explicit bbox their extent drives the
            # H3 resolution and the points/hexagons decision.
            cursor.execute(f"""
                SELECT latitude::float, longitude::float, dose_rate::float
                FROM measures_radiation_measurement
                WHERE {where_sql} AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY RANDOM() LIMIT %(points_limit)s
            """, params)
            map_points = [{'lat': r[0], 'lon': r[1], 'value': r[2]} for r in cursor.fetchall()]

            resolution = explicit_resolution or bbox_to_resolution(bbox_ctx or data_extent(map_points))
            params['resolution'] = resolution
            map_mode = choose_map_mode(map_mode_param, map_points, bbox_ctx)

            # H3 aggregation for map + table
            cursor.execute(f"""
                WITH h3_cells AS (
                    SELECT h3_lat_lng_to_cell(POINT(longitude::float, latitude::float), %(resolution)s) AS h3_index,
                           dose_rate::float AS value
                    FROM measures_radiation_measurement WHERE {where_sql}
                ),
                agg AS (
                    SELECT h3_index,
                           COUNT(*)::int AS measurement_count,
                           AVG(value)::float AS avg_value,
                           MIN(value)::float AS min_value,
                           MAX(value)::float AS max_value,
                           STDDEV(value)::float AS std_value
                    FROM h3_cells GROUP BY h3_index HAVING COUNT(*) >= %(min_count)s
                )
                SELECT h3_index::text, measurement_count,
                       ROUND(avg_value::numeric,6)::float,
                       ROUND(min_value::numeric,6)::float,
                       ROUND(max_value::numeric,6)::float,
                       ROUND(COALESCE(std_value,0)::numeric,6)::float
                FROM agg ORDER BY measurement_count DESC LIMIT 1000
            """, params)
            cols = ['h3_index', 'measurement_count', 'avg_value', 'min_value', 'max_value', 'std_value']
            hexagons = [dict(zip(cols, row)) for row in cursor.fetchall()]

            # Time series (avg dose_rate per day)
            cursor.execute(f"""
                SELECT DATE("dateTime") AS d, AVG(dose_rate)::float, COUNT(*)::int
                FROM measures_radiation_measurement
                WHERE {where_sql}
                GROUP BY d ORDER BY d
            """, params)
            time_rows = cursor.fetchall()

            # Dose rate sample for histogram + classification
            cursor.execute(f"""
                SELECT dose_rate::float
                FROM measures_radiation_measurement
                WHERE {where_sql} AND dose_rate > 0
                ORDER BY RANDOM() LIMIT 5000
            """, params)
            dose_rate_values = [r[0] for r in cursor.fetchall()]

            # CPM sample for histogram
            cursor.execute(f"""
                SELECT cpm::float
                FROM measures_radiation_measurement
                WHERE {where_sql} AND cpm IS NOT NULL AND cpm > 0
                ORDER BY RANDOM() LIMIT 5000
            """, params)
            cpm_values = [r[0] for r in cursor.fetchall()]

            # Scatter: CPM vs dose_rate
            cursor.execute(f"""
                SELECT cpm::float, dose_rate::float
                FROM measures_radiation_measurement
                WHERE {where_sql} AND cpm IS NOT NULL AND cpm > 0 AND dose_rate > 0
                ORDER BY RANDOM() LIMIT 2000
            """, params)
            cpm_dose_scatter = [{'cpm': r[0], 'dose_rate': r[1]} for r in cursor.fetchall()]

            # Scatter: altitude vs dose_rate
            cursor.execute(f"""
                SELECT altitude::float, dose_rate::float
                FROM measures_radiation_measurement
                WHERE {where_sql} AND altitude IS NOT NULL AND dose_rate > 0
                ORDER BY RANDOM() LIMIT 3000
            """, params)
            altitude_dose_data = [{'altitude': r[0], 'dose_rate': r[1]} for r in cursor.fetchall()]

            # Hour distribution
            cursor.execute(f"""
                SELECT EXTRACT(HOUR FROM "dateTime")::int AS hour,
                       COUNT(*)::int, AVG(dose_rate)::float
                FROM measures_radiation_measurement
                WHERE {where_sql}
                GROUP BY hour ORDER BY hour
            """, params)
            hour_data = [{'hour': r[0], 'count': r[1], 'avg_dose_rate': r[2]} for r in cursor.fetchall()]

            # Unique users
            cursor.execute(f"""
                SELECT COUNT(DISTINCT user_id)::int
                FROM measures_radiation_measurement
                WHERE {where_sql} AND user_id IS NOT NULL
            """, params)
            total_users = cursor.fetchone()[0] or 0

            # Total distance from tracks
            cursor.execute(f"""
                SELECT COALESCE(SUM(t.total_distance), 0)::float
                FROM measures_track t
                WHERE t.id IN (
                    SELECT DISTINCT track_id
                    FROM measures_radiation_measurement
                    WHERE {where_sql} AND track_id IS NOT NULL
                ) AND t.total_distance IS NOT NULL
            """, params)
            total_distance_m = cursor.fetchone()[0] or 0

            # Top contributors (anonymous)
            cursor.execute(f"""
                SELECT user_id, COUNT(*)::int AS cnt
                FROM measures_radiation_measurement
                WHERE {where_sql} AND user_id IS NOT NULL
                GROUP BY user_id ORDER BY cnt DESC LIMIT 10
            """, params)
            top_contributors = [
                {'rank': i + 1, 'measurements': row[1]}
                for i, row in enumerate(cursor.fetchall())
            ]

            # Weather correlation: dose_rate vs temperature, pressure, cloud_cover, rain
            cursor.execute(f"""
                SELECT sub.dose_rate,
                       w.temperature, w.pressure, w.cloud_cover, w.rain_sum, w.humidity
                FROM (
                    SELECT dose_rate::float, weather_cache_id
                    FROM measures_radiation_measurement
                    WHERE {where_sql} AND dose_rate > 0 AND weather_cache_id IS NOT NULL
                    ORDER BY RANDOM() LIMIT 3000
                ) sub
                JOIN measures_weathercache w ON sub.weather_cache_id = w.id
                WHERE w.fetched = TRUE
            """, params)
            weather_dose_data = [
                {
                    'dose_rate':  r[0],
                    'temperature': r[1],
                    'pressure':   r[2],
                    'cloud_cover': r[3],
                    'rain_sum':   r[4],
                    'humidity':   r[5],
                }
                for r in cursor.fetchall()
            ]

        # ── 3. Build stats ────────────────────────────────────────────────
        stats = {}
        if hexagons:
            stats = {
                'global_avg': round(sum(h['avg_value'] for h in hexagons) / len(hexagons), 6),
                'global_min': round(min(h['min_value'] for h in hexagons), 6),
                'global_max': round(max(h['max_value'] for h in hexagons), 6),
            }
        if cpm_values:
            stats['global_avg_cpm'] = round(sum(cpm_values) / len(cpm_values), 1)

        total_measurements = sum(h['measurement_count'] for h in hexagons)

        altitude_stats = None
        if altitude_dose_data:
            alts = [d['altitude'] for d in altitude_dose_data]
            altitude_stats = {
                'min': round(min(alts), 0),
                'max': round(max(alts), 0),
                'avg': round(sum(alts) / len(alts), 0),
            }

        # ── 4. Generate charts ────────────────────────────────────────────
        from .report_generators import generate_weather_dose_scatter
        logger.info(f"Radiation report: {len(hexagons)} hexagons, {len(dose_rate_values)} dose values, {len(weather_dose_data)} weather pts")
        if map_mode == 'points':
            map_img = generate_points_map(map_points, bbox=bbox_ctx, value_label=gettext('Dose rate (μSv/h)'))
        else:
            map_img = generate_h3_map(hexagons, resolution, bbox=bbox_ctx, value_label=gettext('Mean dose (μSv/h)'))
        dose_hist_img        = generate_dose_rate_histogram(dose_rate_values)
        cpm_hist_img         = generate_cpm_histogram(cpm_values)
        cpm_dose_scatter_img = generate_dose_rate_cpm_scatter(cpm_dose_scatter)
        classification_img   = generate_radiation_classification_chart(dose_rate_values)
        altitude_scatter_img = generate_altitude_dose_scatter(altitude_dose_data)
        weather_scatter_img  = generate_weather_dose_scatter(weather_dose_data)
        hour_img             = generate_radiation_hour_distribution(hour_data)
        time_data            = [{'date': r[0], 'avg_dose_rate': r[1], 'count': r[2]} for r in time_rows]
        timeseries_img       = generate_radiation_time_series(time_data)

        # ── 5. Resolve names ─────────────────────────────────────────────
        from missions.models import Project, Mission, Campaign
        from .models import Track as TrackModel
        qp = request.query_params

        def _resolve(model, pk, fallback=None):
            if not pk:
                return fallback
            try:
                return model.objects.get(id=pk).name
            except Exception:
                return f'#{pk}'

        project_id  = qp.get('project')
        mission_id  = qp.get('mission')
        campaign_id = qp.get('campaign')
        track_id    = qp.get('track')
        project_name  = _resolve(Project,    project_id,  gettext('All projects'))
        mission_name  = _resolve(Mission,    mission_id)
        campaign_name = _resolve(Campaign,   campaign_id)
        track_name    = _resolve(TrackModel, track_id)

        # ── 6. Render HTML + generate PDF ─────────────────────────────────
        context = {
            'project_name':        project_name,
            'date_from':           qp.get('start_date') or '—',
            'date_to':             qp.get('end_date') or '—',
            'generated_at':        report_generated_at(dt.datetime.now(), lang),
            'total_measurements':  total_measurements,
            'total_hexagons':      len(hexagons),
            'map_mode':            map_mode,
            'map_points':          len(map_points),
            'map_sampled':         len(map_points) < total_measurements,
            'total_days':          len(time_rows),
            'total_users':         total_users,
            'total_distance_km':   round(total_distance_m / 1000, 1) if total_distance_m else 0,
            'resolution':          resolution,
            'stats':               stats,
            'altitude_stats':      altitude_stats,
            'hexagons':            hexagons[:30],
            'map_img':             map_img,
            'dose_hist_img':       dose_hist_img,
            'cpm_hist_img':        cpm_hist_img,
            'cpm_dose_scatter_img': cpm_dose_scatter_img,
            'classification_img':  classification_img,
            'altitude_scatter_img': altitude_scatter_img,
            'weather_scatter_img': weather_scatter_img,
            'timeseries_img':      timeseries_img,
            'hour_img':            hour_img,
            'top_contributors':    top_contributors,
            'filter_project':      project_id,
            'filter_project_name': project_name if project_id else None,
            'filter_mission':      mission_id,
            'filter_mission_name': mission_name,
            'filter_campaign':     campaign_id,
            'filter_campaign_name': campaign_name,
            'filter_track':        track_id,
            'filter_track_name':   track_name,
            'filter_start_date':   qp.get('start_date'),
            'filter_end_date':     qp.get('end_date'),
            'filter_bbox':         bbox_ctx,
            'filter_min_dose_rate': qp.get('min_dose_rate'),
            'filter_max_dose_rate': qp.get('max_dose_rate'),
            'filter_min_altitude': qp.get('min_altitude'),
            'filter_max_altitude': qp.get('max_altitude'),
            'filter_min_speed':    qp.get('min_speed'),
            'filter_max_speed':    qp.get('max_speed'),
        }

        html_string = render_to_string('reports/radiation_report.html', context)
        pdf_file    = HTML(string=html_string, base_url='/').write_pdf()

        filename = f"radiation_report_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


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
        if self.action in ['list', 'retrieve', 'h3_aggregation', 'h3_aggregation_vertex', 'count', 'paginated']:
            return [AllowAny()]
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

    def filter_queryset(self, queryset):
        """
        Apply query-param filters (project, campaign, mission, track, device,
        date range, bounding box, altitude/lux/speed ranges).

        Centralizing them here means list(), count() and paginated() filter
        identically — all three call self.filter_queryset().
        """
        from django.utils.dateparse import parse_date
        request = self.request

        track_id = request.query_params.get('track')
        project_id = request.query_params.get('project')
        mission_id = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        device_id = request.query_params.get('device')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east = request.query_params.get('east')
        west = request.query_params.get('west')

        min_altitude = request.query_params.get('min_altitude')
        max_altitude = request.query_params.get('max_altitude')
        min_lux = request.query_params.get('min_lux')
        max_lux = request.query_params.get('max_lux')
        min_speed = request.query_params.get('min_speed')
        max_speed = request.query_params.get('max_speed')

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

        if start_date:
            parsed_date = parse_date(start_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__gte=timezone.make_aware(datetime.combine(parsed_date, datetime.min.time())))
        if end_date:
            parsed_date = parse_date(end_date)
            if parsed_date:
                queryset = queryset.filter(dateTime__lte=timezone.make_aware(datetime.combine(parsed_date, datetime.max.time())))

        if north and south and east and west:
            try:
                bbox = Polygon.from_bbox((float(west), float(south), float(east), float(north)))
                bbox.srid = 4326
                queryset = queryset.filter(location__within=bbox)
            except ValueError:
                pass
        else:
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

        if min_altitude:
            try:
                queryset = queryset.filter(altitude__gte=float(min_altitude))
            except ValueError:
                pass
        if max_altitude:
            try:
                queryset = queryset.filter(altitude__lte=float(max_altitude))
            except ValueError:
                pass

        if min_lux:
            try:
                queryset = queryset.filter(lux__gte=float(min_lux))
            except ValueError:
                pass
        if max_lux:
            try:
                queryset = queryset.filter(lux__lte=float(max_lux))
            except ValueError:
                pass

        if min_speed:
            try:
                queryset = queryset.filter(speed__gte=float(min_speed))
            except ValueError:
                pass
        if max_speed:
            try:
                queryset = queryset.filter(speed__lte=float(max_speed))
            except ValueError:
                pass

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
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter measurements from this date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter measurements up to this date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box: maximum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box: minimum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box: maximum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box: minimum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_altitude', openapi.IN_QUERY, description="Minimum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_altitude', openapi.IN_QUERY, description="Maximum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_lux', openapi.IN_QUERY, description="Minimum lux value", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_lux', openapi.IN_QUERY, description="Maximum lux value", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_speed', openapi.IN_QUERY, description="Minimum speed in m/s", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_speed', openapi.IN_QUERY, description="Maximum speed in m/s", type=openapi.TYPE_NUMBER),
        ]
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

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
            openapi.Parameter('mission', openapi.IN_QUERY, description="Filter by mission ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('min_count', openapi.IN_QUERY, description="Minimum measurements per hexagon (default: 1)", type=openapi.TYPE_INTEGER, default=1),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter measurements from this date (format: YYYY-MM-DD, example: 2024-01-15)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter measurements until this date (format: YYYY-MM-DD, example: 2024-12-31)", type=openapi.TYPE_STRING),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box: maximum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box: minimum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box: maximum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box: minimum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_altitude', openapi.IN_QUERY, description="Minimum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_altitude', openapi.IN_QUERY, description="Maximum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_lux', openapi.IN_QUERY, description="Minimum lux value", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_lux', openapi.IN_QUERY, description="Maximum lux value", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_speed', openapi.IN_QUERY, description="Minimum speed in m/s", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_speed', openapi.IN_QUERY, description="Maximum speed in m/s", type=openapi.TYPE_NUMBER),
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
    @staticmethod
    def _build_lp_where(request):
        """
        Build the WHERE clause and params dict shared by h3_aggregation and report.
        Returns (where_clauses, params, bbox_ctx, error_response).
        error_response is a DRF Response if a validation error occurred, else None.
        bbox_ctx is a dict {north, south, east, west} when all four are present, else None.
        """
        from django.utils.dateparse import parse_date

        where_clauses = ["latitude IS NOT NULL", "longitude IS NOT NULL", "lux IS NOT NULL"]
        params = {}

        project_id  = request.query_params.get('project')
        mission_id  = request.query_params.get('mission')
        campaign_id = request.query_params.get('campaign')
        track_id    = request.query_params.get('track')
        start_date  = request.query_params.get('start_date')
        end_date    = request.query_params.get('end_date')
        north = request.query_params.get('north')
        south = request.query_params.get('south')
        east  = request.query_params.get('east')
        west  = request.query_params.get('west')
        min_lux      = request.query_params.get('min_lux')
        max_lux      = request.query_params.get('max_lux')
        min_altitude = request.query_params.get('min_altitude')
        max_altitude = request.query_params.get('max_altitude')
        min_speed    = request.query_params.get('min_speed')
        max_speed    = request.query_params.get('max_speed')

        if project_id:
            where_clauses.append("project_id = %(project_id)s")
            params['project_id'] = project_id

        if mission_id:
            where_clauses.append("campaign_id IN (SELECT id FROM missions_campaign WHERE mission_id = %(mission_id)s)")
            params['mission_id'] = mission_id

        if campaign_id:
            where_clauses.append("campaign_id = %(campaign_id)s")
            params['campaign_id'] = campaign_id

        if track_id:
            where_clauses.append("track_id = %(track_id)s")
            params['track_id'] = track_id

        bbox_ctx = None
        if north is not None and south is not None and east is not None and west is not None:
            try:
                n, s, e, w = float(north), float(south), float(east), float(west)
                where_clauses.append(
                    "ST_Within(location::geometry, ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326))"
                )
                params.update({'north': n, 'south': s, 'east': e, 'west': w})
                bbox_ctx = {'north': n, 'south': s, 'east': e, 'west': w}
            except ValueError:
                return where_clauses, params, None, Response(
                    {'error': 'Invalid bounding box parameters.'}, status=status.HTTP_400_BAD_REQUEST
                )
        else:
            for val, key, clause in [
                (north, 'north', "latitude <= %(north)s"),
                (south, 'south', "latitude >= %(south)s"),
                (east,  'east',  "longitude <= %(east)s"),
                (west,  'west',  "longitude >= %(west)s"),
            ]:
                if val is not None:
                    try:
                        params[key] = float(val)
                        where_clauses.append(clause)
                    except ValueError:
                        return where_clauses, params, None, Response(
                            {'error': f'Invalid {key} parameter.'}, status=status.HTTP_400_BAD_REQUEST
                        )

        if start_date:
            parsed = parse_date(start_date)
            if parsed:
                params['start_date'] = timezone.make_aware(datetime.combine(parsed, datetime.min.time()))
                where_clauses.append('"dateTime" >= %(start_date)s')
            else:
                return where_clauses, params, None, Response(
                    {'error': 'Invalid start_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST
                )

        if end_date:
            parsed = parse_date(end_date)
            if parsed:
                params['end_date'] = timezone.make_aware(datetime.combine(parsed, datetime.max.time()))
                where_clauses.append('"dateTime" <= %(end_date)s')
            else:
                return where_clauses, params, None, Response(
                    {'error': 'Invalid end_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST
                )

        for val, key, clause in [
            (min_lux,      'min_lux',      "lux >= %(min_lux)s"),
            (max_lux,      'max_lux',      "lux <= %(max_lux)s"),
            (min_altitude, 'min_altitude', "(altitude IS NULL OR altitude >= %(min_altitude)s)"),
            (max_altitude, 'max_altitude', "(altitude IS NULL OR altitude <= %(max_altitude)s)"),
            (min_speed,    'min_speed',    "(speed IS NULL OR speed >= %(min_speed)s)"),
            (max_speed,    'max_speed',    "(speed IS NULL OR speed <= %(max_speed)s)"),
        ]:
            if val is not None:
                try:
                    params[key] = float(val)
                    where_clauses.append(clause)
                except ValueError:
                    return where_clauses, params, None, Response(
                        {'error': f'Invalid {key} parameter.'}, status=status.HTTP_400_BAD_REQUEST
                    )

        return where_clauses, params, bbox_ctx, None

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
        
        where_clauses, params, _bbox_ctx, err = self._build_lp_where(request)
        if err:
            return err
        params['resolution'] = resolution
        params['min_count'] = min_count
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
                lux::float as value,
                cct::float as cct_value
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
                STDDEV(value)::float as std_value,
                AVG(cct_value)::float as avg_cct,
                MIN(cct_value)::float as min_cct,
                MAX(cct_value)::float as max_cct
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
            ROUND(COALESCE(std_value, 0)::numeric, 6)::float as std_value,
            ROUND(COALESCE(avg_cct, 0)::numeric, 2)::float as avg_cct,
            ROUND(COALESCE(min_cct, 0)::numeric, 2)::float as min_cct,
            ROUND(COALESCE(max_cct, 0)::numeric, 2)::float as max_cct
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
            all_avg_cct = [r['avg_cct'] for r in results if r['avg_cct'] and r['avg_cct'] > 0]
            statistics = {
                'global_avg': round(sum(all_avg_values) / len(all_avg_values), 6),
                'global_min': round(min(r['min_value'] for r in results), 6),
                'global_max': round(max(r['max_value'] for r in results), 6),
            }
            if all_avg_cct:
                statistics['global_avg_cct'] = round(sum(all_avg_cct) / len(all_avg_cct), 2)
                valid_min_cct = [r['min_cct'] for r in results if r['min_cct'] is not None]
                valid_max_cct = [r['max_cct'] for r in results if r['max_cct'] is not None]
                if valid_min_cct:
                    statistics['global_min_cct'] = round(min(valid_min_cct), 2)
                if valid_max_cct:
                    statistics['global_max_cct'] = round(max(valid_max_cct), 2)
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
        operation_description="Generate a PDF report for the current selection. Accepts the same filters as h3-aggregation.",
        manual_parameters=[
            openapi.Parameter('lang', openapi.IN_QUERY, description="Report language: 'en' or 'es' (default: Accept-Language header, then REPORT_DEFAULT_LANGUAGE)", type=openapi.TYPE_STRING),
            openapi.Parameter('map_mode', openapi.IN_QUERY, description="Map style: 'auto' (default: points when the area spans ≤1.5°, hexagons otherwise), 'points' or 'hexagons'", type=openapi.TYPE_STRING),
            openapi.Parameter('resolution', openapi.IN_QUERY, description="H3 resolution for the hexagon map (0-15, default: auto from the map extent)", type=openapi.TYPE_INTEGER),
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('mission', openapi.IN_QUERY, description="Filter by mission ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('campaign', openapi.IN_QUERY, description="Filter by campaign ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box: max latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box: min latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box: max longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box: min longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_lux', openapi.IN_QUERY, description="Minimum lux value", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_lux', openapi.IN_QUERY, description="Maximum lux value", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_altitude', openapi.IN_QUERY, description="Minimum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_altitude', openapi.IN_QUERY, description="Maximum altitude in meters", type=openapi.TYPE_NUMBER),
            openapi.Parameter('min_speed', openapi.IN_QUERY, description="Minimum speed in m/s", type=openapi.TYPE_NUMBER),
            openapi.Parameter('max_speed', openapi.IN_QUERY, description="Maximum speed in m/s", type=openapi.TYPE_NUMBER),
        ],
        responses={200: openapi.Response(description="PDF file", schema=openapi.Schema(type=openapi.TYPE_FILE))}
    )
    @action(detail=False, methods=['get'], url_path='report')
    def report(self, request):
        lang = report_language(request)
        with translation.override(lang):
            return self._render_report(request, lang)

    def _render_report(self, request, lang):
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        from django.utils.dateparse import parse_date
        from weasyprint import HTML
        from .report_generators import (
            generate_h3_map, generate_lux_histogram,
            generate_time_series, generate_lux_cct_scatter,
            generate_hour_distribution,
        )
        import datetime as dt

        # ── 1. Parse filters (identical to h3_aggregation via shared helper) ──
        where_clauses, params, bbox_ctx, err = self._build_lp_where(request)
        if err:
            return err

        from .report_generators import (
            bbox_to_resolution, generate_points_map, choose_map_mode, data_extent, MAP_POINTS_LIMIT,
        )
        try:
            explicit_resolution = max(0, min(15, int(request.query_params.get('resolution', 0))))
        except ValueError:
            explicit_resolution = 0
        map_mode_param = request.query_params.get('map_mode')

        params['min_count'] = 1
        params['points_limit'] = MAP_POINTS_LIMIT
        # Drop bogus GPS fixes at "null island" (0,0 ± noise): they stretch the map
        # to the Gulf of Guinea and no real measurement is taken there.
        where_clauses.append("NOT (abs(latitude) < 0.01 AND abs(longitude) < 0.01)")
        where_sql = " AND ".join(where_clauses)

        # ── 2. Run queries ───────────────────────────────────────────────
        with connection.cursor() as cursor:

            # Individual points for the map (random sample when over the limit).
            # Fetched first: without an explicit bbox their extent drives the
            # H3 resolution and the points/hexagons decision.
            cursor.execute(f"""
                SELECT latitude::float, longitude::float, lux::float
                FROM measures_light_pollution_measurement
                WHERE {where_sql} AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY RANDOM() LIMIT %(points_limit)s
            """, params)
            map_points = [{'lat': r[0], 'lon': r[1], 'value': r[2]} for r in cursor.fetchall()]

            resolution = explicit_resolution or bbox_to_resolution(bbox_ctx or data_extent(map_points))
            params['resolution'] = resolution
            map_mode = choose_map_mode(map_mode_param, map_points, bbox_ctx)

            # H3 aggregation for map + table
            cursor.execute(f"""
                WITH h3_cells AS (
                    SELECT h3_lat_lng_to_cell(POINT(longitude::float, latitude::float), %(resolution)s) AS h3_index,
                           lux::float AS value, cct::float AS cct_value
                    FROM measures_light_pollution_measurement WHERE {where_sql}
                ),
                agg AS (
                    SELECT h3_index,
                           COUNT(*)::int AS measurement_count,
                           AVG(value)::float AS avg_value,
                           MIN(value)::float AS min_value,
                           MAX(value)::float AS max_value,
                           STDDEV(value)::float AS std_value,
                           AVG(cct_value)::float AS avg_cct
                    FROM h3_cells GROUP BY h3_index HAVING COUNT(*) >= %(min_count)s
                )
                SELECT h3_index::text, measurement_count,
                       ROUND(avg_value::numeric,4)::float,
                       ROUND(min_value::numeric,4)::float,
                       ROUND(max_value::numeric,4)::float,
                       ROUND(COALESCE(std_value,0)::numeric,4)::float,
                       ROUND(COALESCE(avg_cct,0)::numeric,2)::float
                FROM agg ORDER BY measurement_count DESC LIMIT 1000
            """, params)
            cols = ['h3_index','measurement_count','avg_value','min_value','max_value','std_value','avg_cct']
            hexagons = [dict(zip(cols, row)) for row in cursor.fetchall()]

            # Time series (avg lux per day)
            cursor.execute(f"""
                SELECT DATE("dateTime") AS d, AVG(lux)::float, COUNT(*)::int
                FROM measures_light_pollution_measurement
                WHERE {where_sql}
                GROUP BY d ORDER BY d
            """, params)
            time_rows = cursor.fetchall()

            # Lux sample for histogram (raw values, log-spaced bins in Python)
            cursor.execute(f"""
                SELECT lux::float
                FROM measures_light_pollution_measurement
                WHERE {where_sql} AND lux > 0
                ORDER BY RANDOM() LIMIT 5000
            """, params)
            lux_values = [r[0] for r in cursor.fetchall()]
            total_count = len(lux_values)

            # Scatter sample (lux vs cct)
            cursor.execute(f"""
                SELECT lux::float, cct::float
                FROM measures_light_pollution_measurement
                WHERE {where_sql} AND cct IS NOT NULL AND cct > 1000 AND cct < 7500
                ORDER BY RANDOM() LIMIT 2000
            """, params)
            scatter_data = [{'lux': r[0], 'cct': r[1]} for r in cursor.fetchall()]

            # Hour distribution
            cursor.execute(f"""
                SELECT EXTRACT(HOUR FROM "dateTime")::int AS hour,
                       COUNT(*)::int, AVG(lux)::float
                FROM measures_light_pollution_measurement
                WHERE {where_sql}
                GROUP BY hour ORDER BY hour
            """, params)
            hour_data = [{'hour': r[0], 'count': r[1], 'avg_lux': r[2]} for r in cursor.fetchall()]

            # CCT histogram sample
            cursor.execute(f"""
                SELECT cct::float
                FROM measures_light_pollution_measurement
                WHERE {where_sql} AND cct IS NOT NULL AND cct > 1000 AND cct < 7500
                ORDER BY RANDOM() LIMIT 5000
            """, params)
            cct_values = [r[0] for r in cursor.fetchall()]

            # Unique users
            cursor.execute(f"""
                SELECT COUNT(DISTINCT user_id)::int
                FROM measures_light_pollution_measurement
                WHERE {where_sql} AND user_id IS NOT NULL
            """, params)
            total_users = cursor.fetchone()[0] or 0

            # Total distance from tracks involved
            cursor.execute(f"""
                SELECT COALESCE(SUM(t.total_distance), 0)::float
                FROM measures_track t
                WHERE t.id IN (
                    SELECT DISTINCT track_id
                    FROM measures_light_pollution_measurement
                    WHERE {where_sql} AND track_id IS NOT NULL
                ) AND t.total_distance IS NOT NULL
            """, params)
            total_distance_m = cursor.fetchone()[0] or 0

            # Top contributors (anonymous: user rank + count)
            cursor.execute(f"""
                SELECT user_id, COUNT(*)::int AS cnt
                FROM measures_light_pollution_measurement
                WHERE {where_sql} AND user_id IS NOT NULL
                GROUP BY user_id ORDER BY cnt DESC LIMIT 10
            """, params)
            top_contributors_raw = cursor.fetchall()
            top_contributors = [
                {'rank': i + 1, 'measurements': row[1]}
                for i, row in enumerate(top_contributors_raw)
            ]

        # ── 3. Build stats ───────────────────────────────────────────────
        stats = {}
        if hexagons:
            all_avg = [h['avg_value'] for h in hexagons]
            all_min = [h['min_value'] for h in hexagons]
            all_max = [h['max_value'] for h in hexagons]
            all_cct = [h['avg_cct'] for h in hexagons if h['avg_cct'] and h['avg_cct'] > 0]
            stats = {
                'global_avg': round(sum(all_avg) / len(all_avg), 4),
                'global_min': round(min(all_min), 4),
                'global_max': round(max(all_max), 4),
            }
            if all_cct:
                stats['global_avg_cct'] = round(sum(all_cct) / len(all_cct), 0)

        total_measurements = sum(h['measurement_count'] for h in hexagons)

        # ── 4. Generate charts (base64 data URIs, no temp files) ────────
        from .report_generators import (
            generate_cct_histogram, generate_ida_chart,
            generate_time_series_trend, generate_night_day_chart,
        )
        logger.info(f"Report: {len(hexagons)} hexagons, {len(lux_values)} lux values, {len(time_rows)} days, {len(hour_data)} hours")
        if map_mode == 'points':
            map_img = generate_points_map(map_points, bbox=bbox_ctx, value_label=gettext('Lux'), log_scale=True)
        else:
            map_img = generate_h3_map(hexagons, resolution, bbox=bbox_ctx)
        histogram_img  = generate_lux_histogram(lux_values)
        cct_hist_img   = generate_cct_histogram(cct_values)
        ida_img        = generate_ida_chart(lux_values)
        scatter_img    = generate_lux_cct_scatter(scatter_data)
        hour_img       = generate_hour_distribution(hour_data)
        night_day_img  = generate_night_day_chart(hour_data)

        time_data = [{'date': r[0], 'avg_lux': r[1], 'count': r[2]} for r in time_rows]
        timeseries_img = generate_time_series_trend(time_data)
        logger.info(f"Report: map={map_img is not None}, hist={histogram_img is not None}, ida={ida_img is not None}, ts={timeseries_img is not None}")

        # ── 5. Resolve names ──────────────────────────────────────────────
        from missions.models import Project, Mission, Campaign
        from .models import Track as TrackModel
        qp = request.query_params

        def _resolve(model, pk, fallback=None):
            if not pk:
                return fallback
            try:
                return model.objects.get(id=pk).name
            except Exception:
                return f'#{pk}'

        project_id  = qp.get('project')
        mission_id  = qp.get('mission')
        campaign_id = qp.get('campaign')
        track_id    = qp.get('track')
        project_name  = _resolve(Project,    project_id,  gettext('All projects'))
        mission_name  = _resolve(Mission,    mission_id)
        campaign_name = _resolve(Campaign,   campaign_id)
        track_name    = _resolve(TrackModel, track_id)

        # ── 6. Render HTML + generate PDF ───────────────────────────────
        context = {
            'project_name': project_name,
            'date_from': qp.get('start_date') or '—',
            'date_to': qp.get('end_date') or '—',
            'generated_at': report_generated_at(dt.datetime.now(), lang),
            'total_measurements': total_measurements,
            'total_hexagons': len(hexagons),
            'map_mode': map_mode,
            'map_points': len(map_points),
            'map_sampled': len(map_points) < total_measurements,
            'total_days': len(time_rows),
            'total_users': total_users,
            'total_distance_km': round(total_distance_m / 1000, 1) if total_distance_m else 0,
            'resolution': resolution,
            'stats': stats,
            'hexagons': hexagons[:30],
            'map_img': map_img,
            'histogram_img': histogram_img,
            'cct_hist_img': cct_hist_img,
            'ida_img': ida_img,
            'scatter_img': scatter_img,
            'timeseries_img': timeseries_img,
            'hour_img': hour_img,
            'night_day_img': night_day_img,
            'top_contributors': top_contributors,
            'filter_project':      project_id,
            'filter_project_name': project_name if project_id else None,
            'filter_mission':      mission_id,
            'filter_mission_name': mission_name,
            'filter_campaign':     campaign_id,
            'filter_campaign_name': campaign_name,
            'filter_track':        track_id,
            'filter_track_name':   track_name,
            'filter_start_date': qp.get('start_date'),
            'filter_end_date': qp.get('end_date'),
            'filter_bbox': bbox_ctx,
            'filter_min_lux': qp.get('min_lux'),
            'filter_max_lux': qp.get('max_lux'),
            'filter_min_altitude': qp.get('min_altitude'),
            'filter_max_altitude': qp.get('max_altitude'),
            'filter_min_speed': qp.get('min_speed'),
            'filter_max_speed': qp.get('max_speed'),
        }

        html_string = render_to_string('reports/light_pollution_report.html', context)
        pdf_file = HTML(string=html_string, base_url='/').write_pdf()

        filename = f"light_pollution_report_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

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

class IsTrackOwnerOrReadOnly(BasePermission):
    """Only the track's creator can modify or delete it; reads follow the queryset."""

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.created_by_id == getattr(request.user, 'id', None)


class SpectrumPagination(PageNumberPagination):
    """
    Page-number pagination for spectra, producing the standard DRF shape
    {count, next, previous, results}. The client follows `next` until exhausted.
    """
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000


class RadiationSpectrumViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API for gamma spectra (RadiaCode), nested under a track.

    Endpoints:
        GET /api/radiation-spectra/?track={track_id} - List a track's spectra
        GET /api/radiation-spectra/{id}/             - Retrieve a single spectrum

    Public read access, like the measurement endpoints. Results are filtered by
    the `track` query param and ordered by `index` (then `started_at`).
    Paginated with {count, next, previous, results}; each item includes the full
    `counts` array (this endpoint is only called when opening a track).
    """
    queryset = Spectrum.objects.select_related('track').all()
    serializer_class = SpectrumSerializer
    pagination_class = SpectrumPagination

    def get_permissions(self):
        """GET is public; any write actions (none exposed) require auth."""
        if self.action in ['list', 'retrieve', 'map']:
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Detectar si es una vista falsa de Swagger
        if getattr(self, 'swagger_fake_view', False):
            return queryset.none()

        track_id = self.request.query_params.get('track')
        if track_id:
            queryset = queryset.filter(track_id=track_id)

        return queryset.order_by('index', 'started_at')

    @swagger_auto_schema(
        tags=['Spectra'],
        operation_description=(
            "List gamma spectra (public access). Filter by track with ?track={id}. "
            "Paginated; follow `next` until null. Each item includes the full counts array."
        ),
        manual_parameters=[
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Items per page (max 1000, default 100)", type=openapi.TYPE_INTEGER),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Spectra'],
        operation_description="Retrieve a single gamma spectrum by ID with full detail, including counts (public access)."
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Spectra'],
        operation_description=(
            "Lightweight list of geolocated spectra for map markers: only id, latitude and "
            "longitude (segment start coordinates). Counts are NOT loaded. Spectra without a "
            "GPS fix are excluded. Returns a flat array (not paginated). Optional filters: "
            "track, project, and bounding box (north/south/east/west). Fetch full detail per "
            "marker via GET /api/radiation-spectra/{id}/."
        ),
        manual_parameters=[
            openapi.Parameter('track', openapi.IN_QUERY, description="Filter by track ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('north', openapi.IN_QUERY, description="Bounding box: maximum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('south', openapi.IN_QUERY, description="Bounding box: minimum latitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('east', openapi.IN_QUERY, description="Bounding box: maximum longitude", type=openapi.TYPE_NUMBER),
            openapi.Parameter('west', openapi.IN_QUERY, description="Bounding box: minimum longitude", type=openapi.TYPE_NUMBER),
        ],
        responses={
            200: openapi.Response(
                description="Flat array of geolocated spectra",
                examples={"application/json": [{"id": 123, "latitude": 40.41, "longitude": -3.70}]}
            )
        }
    )
    @action(detail=False, methods=['get'], url_path='map')
    def map(self, request):
        """
        Lightweight markers for the general map: id + latitude + longitude only.

        Uses .values() so the heavy `counts` column is never selected. Only
        spectra with start coordinates are returned (no GPS fix => not mappable).
        """
        queryset = Spectrum.objects.filter(
            start_lat__isnull=False,
            start_lon__isnull=False,
        )

        track_id = request.query_params.get('track')
        if track_id:
            queryset = queryset.filter(track_id=track_id)

        project_id = request.query_params.get('project')
        if project_id:
            queryset = queryset.filter(track__project_id=project_id)

        # Optional bounding box on the segment start coordinates
        for param, field, lookup in [
            ('north', 'start_lat', 'lte'),
            ('south', 'start_lat', 'gte'),
            ('east', 'start_lon', 'lte'),
            ('west', 'start_lon', 'gte'),
        ]:
            value = request.query_params.get(param)
            if value is not None:
                try:
                    queryset = queryset.filter(**{f'{field}__{lookup}': float(value)})
                except ValueError:
                    return Response(
                        {'error': f'Invalid {param} parameter.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        rows = queryset.order_by('index', 'started_at').values('id', 'start_lat', 'start_lon')
        results = [
            {'id': r['id'], 'latitude': r['start_lat'], 'longitude': r['start_lon']}
            for r in rows
        ]
        return Response(results)


def _looks_like_rctrk(head):
    """
    Return True if the first bytes of an upload look like a RadiaCode RCTRK export.

    Android: text whose first line starts with "Track:".
    iOS: a JSON object whose first key-ish content mentions "markers" (the full
    parse happens later in the worker; this only rejects obvious garbage).
    """
    if isinstance(head, bytes):
        head = head.decode('utf-8', errors='replace')
    head = head.lstrip('\ufeff \t\r\n')
    if head.startswith('Track:'):
        return True
    if head.startswith('{'):
        return '"markers"' in head or '"title"' in head or '"devices"' in head
    return False


class TrackViewSet(viewsets.ModelViewSet):
    # Scope for ScopedRateThrottle; only the actions that declare
    # throttle_classes=[ScopedRateThrottle] (upload, upload_json) are throttled.
    throttle_scope = 'track_upload'
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
        Requires authentication for all actions; ownership is enforced at object level
        for unsafe methods so that widening get_queryset() in the future cannot
        accidentally grant delete/update rights on other users' tracks.
        """
        return [IsAuthenticated(), IsTrackOwnerOrReadOnly()]

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
                'mission': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Mission ID'
                ),
                'campaign': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Campaign ID (must belong to the mission)'
                ),
                'campaign_password': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Campaign password (required only if the campaign is protected)'
                ),
                'project': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Project ID (optional; must match mission.project if given)'
                ),
            },
            required=['file', 'device', 'mission', 'campaign']
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
            401: 'Not authenticated',
            403: 'Wrong campaign password'
        }
    )
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated],
            throttle_classes=[ScopedRateThrottle])
    def upload(self, request):
        """
        Upload a track file - processing happens asynchronously with RQ
        
        Only RCTRK format (RadiaCode) is supported. Both platform exports are
        accepted and detected automatically (see measures.tasks.detect_rctrk_format):

        Android (tab-separated text):
           Track: <name>\t<device>\t \tEC
           Timestamp\tTime\tLatitude\tLongitude\tAccuracy\tDoseRate\tCountRate\tComment
           134007603713620000\t2025-08-27 09:26:11\t41.2184571\t-1.1548868\t1.94\t6.52\t7.47\t 

        iOS (JSON):
           {"title": "...", "devices": ["RC-102-..."], "start": 1763278422,
            "markers": [{"date": 1763283709, "lat": 41.8, "lon": -1.8,
                         "acc": 5, "doseRate": 5.73, "countRate": 5.38}, ...]}
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
        
        # Detect file type
        file_extension = file.name.split('.')[-1].lower()
        if file_extension != 'rctrk':
            return Response(
                {'error': f'Unsupported file type: {file_extension}. Only RCTRK files are supported.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Cheap content check before storing anything: an RCTRK is either the
        # Android tab-separated export ("Track: ..." first line) or the iOS JSON
        # export (an object with a "markers" list).
        head = file.read(4096)
        file.seek(0)
        if not _looks_like_rctrk(head):
            return Response(
                {'error': 'File content is not a RadiaCode RCTRK export (Android text or iOS JSON).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Same rules as upload_json: mission + campaign are required, the campaign
        # must belong to the mission, the project is derived from the mission and
        # a protected campaign needs its password.
        missing = [name for name, value in (('device', device_id), ('mission', mission_id), ('campaign', campaign_id)) if not value]
        if missing:
            return Response(
                {'error': f"Missing required field(s): {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            device = Device.objects.get(id=device_id)
        except (Device.DoesNotExist, ValueError, TypeError):
            return Response(
                {'error': f'Device with id {device_id} not found'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        from missions.models import Mission, Campaign

        try:
            mission = Mission.objects.get(id=mission_id)
        except (Mission.DoesNotExist, ValueError, TypeError):
            return Response({'error': f'Mission with id {mission_id} not found'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            campaign = Campaign.objects.get(id=campaign_id)
        except (Campaign.DoesNotExist, ValueError, TypeError):
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

        if campaign.password:
            campaign_password = request.data.get('campaign_password')
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
        
        # Create Track object with status='pending'
        track = Track.objects.create(
            created_by=request.user,
            device=device,
            project=project,
            mission=mission,
            campaign=campaign,
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
                'spectra': openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    description='Optional gamma spectra integrated over track segments (RadiaCode only). '
                                'Backward-compatible: may be omitted or empty.',
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        required=['name', 'index', 'startedAt', 'endedAt', 'durationSec',
                                  'a0', 'a1', 'a2', 'channelCount', 'counts'],
                        properties={
                            'name': openapi.Schema(type=openapi.TYPE_STRING),
                            'index': openapi.Schema(type=openapi.TYPE_INTEGER, description='0-based order within the track'),
                            'startedAt': openapi.Schema(type=openapi.TYPE_STRING, format='date-time'),
                            'endedAt': openapi.Schema(type=openapi.TYPE_STRING, format='date-time'),
                            'durationSec': openapi.Schema(type=openapi.TYPE_INTEGER, description='Integration (live) time in seconds'),
                            'a0': openapi.Schema(type=openapi.TYPE_NUMBER, description='Energy calibration: E(keV) = a0 + a1·ch + a2·ch²'),
                            'a1': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'a2': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'channelCount': openapi.Schema(type=openapi.TYPE_INTEGER, description='Number of channels (typically 1024)'),
                            'counts': openapi.Schema(
                                type=openapi.TYPE_ARRAY,
                                items=openapi.Schema(type=openapi.TYPE_INTEGER),
                                description='Counts per channel; len(counts) must equal channelCount',
                            ),
                            'startLat': openapi.Schema(type=openapi.TYPE_NUMBER, description='Position at segment start (nullable)'),
                            'startLon': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'startAlt': openapi.Schema(type=openapi.TYPE_NUMBER),
                            'endLat': openapi.Schema(type=openapi.TYPE_NUMBER, description='Position at segment end (nullable)'),
                            'endLon': openapi.Schema(type=openapi.TYPE_NUMBER),
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
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated],
            throttle_classes=[ScopedRateThrottle])
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

    def filter_queryset(self, queryset):
        """
        Restringir el mapa a las medidas en movimiento.

        list() y paginated() alimentan las capas espaciales, así que no incluyen
        las medidas de estación (serie temporal de un punto fijo, que se pinta
        aparte). count() queda exento: reporta el volumen real del proyecto.
        """
        queryset = super().filter_queryset(queryset)
        return scope_capture_mode_qs(
            self.request, queryset,
            default=None if self.action == 'count' else 'movement',
        )

    def create(self, request, *args, **kwargs):
        # --- TEMPORALMENTE DESHABILITADO (crear medida suelta, endpoint legacy) ---
        # No valida la contraseña de la campaña; la única vía de subida es /api/tracks/upload_json/.
        # Para reactivar: eliminar este método create().
        return Response(
            {'error': 'La creación de medidas sueltas está temporalmente deshabilitada. '
                      'Sube los datos como track JSON: /api/tracks/upload_json/'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones.
        Cuenta mediciones del tipo correcto según el proyecto especificado.

        `total` es el volumen real del proyecto: incluye las medidas de estación.
        En radiación se añade el desglose por modo de captura, porque las capas
        espaciales solo muestran las `movement` y quien las pinta necesita saber
        cuántas va a recibir de verdad.
        """
        project_id = self.request.query_params.get('project', None)

        if project_id:
            try:
                project_id = int(project_id)
                project = Project.objects.get(id=project_id)

                # Contar según el tipo de proyecto
                breakdown = {}
                if project.project_type == 'radiation':
                    counts = RadiationMeasurement.objects.filter(project_id=project_id).aggregate(
                        total=Count('id'),
                        movement=Count('id', filter=Q(capture_mode='movement')),
                        static=Count('id', filter=Q(capture_mode='static')),
                    )
                    total = counts['total']
                    breakdown = {'movement': counts['movement'], 'static': counts['static']}
                elif project.project_type == 'light_pollution':
                    total = LightPollutionMeasurement.objects.filter(project_id=project_id).count()
                else:
                    total = 0

                return Response({
                    'total': total,
                    **breakdown,
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

                # Esta rama construye el queryset a mano, así que el alcance hay
                # que aplicarlo aquí: filter_queryset() no llega a pasar por él.
                queryset = scope_capture_mode_qs(request, queryset)

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


class StationViewSet(viewsets.ModelViewSet):
    """
    CRUD for the authenticated user's static base stations (radiation only).

    A station is bound 1:1 to a device the user owns and files readings under a
    fixed radiation project. Listing is always scoped to the requesting user;
    creation auto-assigns the owner. Status and last_measurement_at are managed
    by the gap-detection watchdog, not editable here.

    Endpoints:
        GET    /api/stations/            - List my stations
        POST   /api/stations/            - Create a station for a device I own
        GET    /api/stations/{id}/       - Retrieve one of my stations
        PUT    /api/stations/{id}/       - Update
        PATCH  /api/stations/{id}/       - Partial update
        DELETE /api/stations/{id}/       - Delete
        GET    /api/stations/mine/       - Alias of list (my stations)
        GET    /api/stations/by-project/ - Public station markers for the map
    """
    serializer_class = StationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return Station.objects.none()
        return Station.objects.filter(user=self.request.user).select_related('device', 'project')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def mine(self, request):
        """List the authenticated user's stations (same as list, explicit endpoint)."""
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        tags=['Stations'],
        operation_description="Public station markers for the map, with their current value",
        manual_parameters=[
            openapi.Parameter('project', openapi.IN_QUERY, description="Filter by project ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('include_inactive', openapi.IN_QUERY, description="Include stations marked as not in service", type=openapi.TYPE_BOOLEAN),
        ],
    )
    @action(detail=False, methods=['get'], url_path='by-project', permission_classes=[AllowAny])
    def by_project(self, request):
        """
        Station markers for the map, publicly readable.

        Stations are a fixed-point time series, so they are excluded from the
        hexagon and point layers and drawn as their own markers instead. Each one
        carries its last reading and its mean dose rate over the last 24h, which
        is what the marker colour is meant to be based on.

        `avg_dose_rate_24h` is null when the station has not reported within the
        window: a stale value must not be painted as if it were current.
        """
        stations = Station.objects.select_related('project').order_by('name')

        project_id = request.query_params.get('project')
        if project_id:
            try:
                stations = stations.filter(project_id=int(project_id))
            except (TypeError, ValueError):
                return Response({'error': 'Invalid project parameter.'}, status=status.HTTP_400_BAD_REQUEST)

        if str(request.query_params.get('include_inactive', '')).lower() not in ('1', 'true', 'yes'):
            stations = stations.filter(is_active=True)

        stations = list(stations)
        station_ids = [s.id for s in stations]

        readings = RadiationMeasurement.objects.filter(station_id__in=station_ids)
        since = timezone.now() - timedelta(hours=24)

        window = {
            row['station_id']: row
            for row in readings.filter(dateTime__gte=since)
                               .values('station_id')
                               .annotate(avg_dose_rate=Avg('dose_rate'), count=Count('id'))
        }
        totals = {
            row['station_id']: row['count']
            for row in readings.values('station_id').annotate(count=Count('id'))
        }
        # DISTINCT ON (PostgreSQL): last reading of each station in one query.
        latest = {
            m.station_id: m
            for m in readings.order_by('station_id', '-dateTime').distinct('station_id')
        }

        results = []
        for station in stations:
            recent = window.get(station.id)
            last = latest.get(station.id)
            avg_24h = recent['avg_dose_rate'] if recent else None
            results.append({
                'id': station.id,
                'name': station.name,
                'project': station.project_id,
                'latitude': float(station.latitude),
                'longitude': float(station.longitude),
                'altitude': station.altitude,
                'status': station.status,
                'is_active': station.is_active,
                'last_measurement_at': station.last_measurement_at,
                'expected_interval_seconds': station.expected_interval_seconds,
                'last_dose_rate': last.dose_rate if last else None,
                'last_dose_rate_at': last.dateTime if last else None,
                'avg_dose_rate_24h': round(float(avg_24h), 6) if avg_24h is not None else None,
                'measurements_24h': recent['count'] if recent else 0,
                'measurements_total': totals.get(station.id, 0),
            })

        return Response(results)

    @swagger_auto_schema(
        tags=['Stations'],
        operation_description="Time series of one station's readings, with the weather of each bucket",
        manual_parameters=[
            openapi.Parameter('range', openapi.IN_QUERY, description="Window back from now: 24h, 7d, 30d, 1y (default 24h). Ignored if start/end are given", type=openapi.TYPE_STRING),
            openapi.Parameter('start', openapi.IN_QUERY, description="Window start, ISO-8601", type=openapi.TYPE_STRING),
            openapi.Parameter('end', openapi.IN_QUERY, description="Window end, ISO-8601 (default: now)", type=openapi.TYPE_STRING),
            openapi.Parameter('interval', openapi.IN_QUERY, description="Bucket size: auto (default), raw, 1m, 5m, 15m, 1h, 6h, 1d", type=openapi.TYPE_STRING),
        ],
    )
    @action(detail=True, methods=['get'], url_path='readings', permission_classes=[AllowAny])
    def readings(self, request, pk=None):
        """
        Time series of one station, for the chart behind the map marker.

        Readings are aggregated server-side into buckets — a station at a 5-min
        cadence is ~105k rows a year, which no chart wants and no browser should
        download. ``interval=auto`` picks a bucket that keeps the response in the
        low hundreds of points; ``interval=raw`` dumps the readings themselves,
        capped, for whoever really wants them.

        Empty buckets are omitted rather than zero-filled: when the station was
        down there is no data, and inventing a point would draw a line across the
        outage. The client can spot the gap against expected_interval_seconds.

        Weather rides along as its own series. It is cached per (H3 cell, hour)
        and a station never moves, so its natural resolution is hourly: it is
        returned hourly, or at the reading bucket when that is coarser.
        """
        station = get_object_or_404(Station, pk=pk)

        window, err = parse_time_window(request)
        if err:
            return err
        start, end = window

        interval, err = resolve_interval(request, start, end)
        if err:
            return err

        base = RadiationMeasurement.objects.filter(
            station=station, dateTime__gte=start, dateTime__lt=end,
        )

        payload = {
            'station': {
                'id': station.id,
                'name': station.name,
                'project': station.project_id,
                'latitude': float(station.latitude),
                'longitude': float(station.longitude),
                'status': station.status,
                'expected_interval_seconds': station.expected_interval_seconds,
            },
            'window': {
                'start': start,
                'end': end,
                'interval': 'raw' if interval is None else interval_label(interval),
            },
            'units': {
                'dose_rate': 'µSv/h', 'cpm': 'counts/min', 'temperature': '°C',
                'humidity': '%', 'pressure': 'hPa', 'wind_speed': 'km/h',
                'cloud_cover': '%', 'rain_sum': 'mm',
            },
        }

        if interval is None:
            rows = list(
                base.order_by('dateTime')
                    .values('dateTime', 'dose_rate', 'cpm')[:RAW_READINGS_CAP + 1]
            )
            payload['window']['truncated'] = len(rows) > RAW_READINGS_CAP
            rows = rows[:RAW_READINGS_CAP]
            payload['series'] = [
                {'t': r['dateTime'], 'dose_rate': r['dose_rate'], 'cpm': r['cpm']}
                for r in rows
            ]
        else:
            payload['series'] = bucketed_readings(station.id, start, end, interval)

        payload['window']['points'] = len(payload['series'])
        payload['stats'] = base.aggregate(
            count=Count('id'),
            dose_rate_avg=Avg('dose_rate'), dose_rate_min=Min('dose_rate'), dose_rate_max=Max('dose_rate'),
            cpm_avg=Avg('cpm'), cpm_min=Min('cpm'), cpm_max=Max('cpm'),
        )
        payload['weather'] = bucketed_weather(station.id, start, end, interval)

        response = Response(payload)
        # The bucket covering the current hour is still filling up.
        response['Cache-Control'] = 'public, max-age=60'
        return response


# ---------------------------------------------------------------------------
# Single-observation ingest (radiation only) for headless devices (M5Stack)
# ---------------------------------------------------------------------------
from rest_framework.views import APIView
from devices.authentication import DeviceTokenAuthentication


def _ingest_first(data, *keys):
    """Return the first present, non-null value among the given payload keys."""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _parse_ingest_datetime(raw, fallback):
    """
    Parse a device-supplied timestamp into an aware datetime.

    Accepts ISO-8601 strings (with or without tz; naive assumed UTC) and Unix
    epoch seconds (int or numeric string). Returns ``fallback`` when absent, and
    raises ValueError on an unparseable value.
    """
    from datetime import timezone as _utc_tz

    if raw is None:
        return fallback
    # Unix epoch seconds
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.strip().lstrip('-').isdigit()):
        return datetime.fromtimestamp(int(raw), tz=_utc_tz.utc)
    dt = parse_datetime(str(raw))
    if dt is None:
        raise ValueError(f"Fecha/hora no válida: {raw!r}")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, _utc_tz.utc)
    return dt


#: What a device token may ingest. Movement is closed for now: the phone uploads
#: its points as a track (/api/tracks/upload_json/, authenticated as the user), so
#: nothing legitimate needs it — the only movement rows ever ingested by token were
#: 43 test readings in May. Leaving it open would let a stolen token insert rows at
#: the rate limit's ceiling with nothing to stop it, since the cadence guard below
#: only governs stations. Re-open by adding 'movement' back here.
INGEST_ALLOWED_CAPTURE_MODES = ('static',)


#: Cadence guard for station ingest. A station declares its own cadence in
#: expected_interval_seconds, so we do not hardcode "one every 5 minutes": a
#: station configured to report every 60s would be locked out by that. We accept
#: a reading once its interval has almost elapsed (a little slack for network
#: jitter), never below the floor — otherwise anyone could configure their way
#: out of the limit by declaring a 1-second cadence.
STATION_INGEST_GRACE = 0.8
STATION_MIN_INTERVAL_SECONDS = 30


def _claim_station_slot(station):
    """
    Atomically claim this station's next ingest slot, on SERVER time.

    Returns (True, 0) when the reading is due and the slot has been taken, or
    (False, seconds_to_wait) when it arrived too soon.

    Two things matter here. The window is measured against the server clock, not
    against the payload's dateTime: the device's clock is whatever the caller
    says it is, so trusting it would hand the limit to the attacker. And the
    check is a single conditional UPDATE rather than read-then-write, because two
    requests racing across gunicorn workers would both pass a read-then-write
    check; this way Postgres arbitrates and exactly one of them matches a row.
    """
    min_interval = max(
        STATION_MIN_INTERVAL_SECONDS,
        int(station.expected_interval_seconds * STATION_INGEST_GRACE),
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE measures_station
               SET last_measurement_at = NOW(),
                   status = 'online',
                   updated_at = NOW()
             WHERE id = %s
               AND (last_measurement_at IS NULL
                    OR last_measurement_at <= NOW() - (%s * INTERVAL '1 second'))
            """,
            [station.id, min_interval],
        )
        if cursor.rowcount == 1:
            return True, 0

        # Turned away. How long to wait comes from the row, not from the station
        # object in memory: the UPDATE above (this request's or a concurrent one's)
        # has already moved last_measurement_at on, and the in-memory copy predates it.
        cursor.execute(
            """
            SELECT CEIL(EXTRACT(EPOCH FROM (
                       last_measurement_at + (%s * INTERVAL '1 second') - NOW()
                   )))::int
              FROM measures_station
             WHERE id = %s
            """,
            [min_interval, station.id],
        )
        row = cursor.fetchone()

    return False, max(1, row[0] if row and row[0] is not None else min_interval)


class RadiationIngestView(APIView):
    """
    Ingest a single radiation observation from a headless device (e.g. M5Stack).

    POST /api/ingest/radiation/
    Auth: device ingest token in the ``X-Device-Token`` header (NOT a user token).
    The device — and therefore its owner — is resolved from the token; the firmware
    never sends device/user identity.

    Two capture modes (the firmware states which on each reading):

      movement (default): a loose point to be grouped into a track later by the
        sessionization job. The payload carries ``project`` and ``latitude`` /
        ``longitude``.

      static: a base-station time-series reading. The station is derived from the
        token (token -> device -> device.station); ``project`` and location come
        from the station, so they are NOT read from the payload. The station's
        watchdog state (last_measurement_at, status) is bumped here.

    Common: ``campaign`` is always NULL (mission therefore None), ``received_at``
    is stamped server-side, and duplicates are deduped by ``(device, dateTime)``
    so retries from a flaky link are idempotent. Weather is NOT handled here for
    either mode: the periodic ``assign_pending_weather_buckets`` sweeper buckets
    every measurement lacking a weather_cache, and ``fetch_pending_weather`` fills
    the buckets — one unified path for tracks, movement and stations.
    """
    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        device = request.auth
        if not isinstance(device, Device):
            return Response(
                {'error': 'Se requiere autenticación de dispositivo (cabecera X-Device-Token).'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        data = request.data
        capture_mode = str(_ingest_first(data, 'capture_mode', 'mode_capture') or 'movement').lower()
        if capture_mode not in ('movement', 'static'):
            return Response(
                {'error': "capture_mode debe ser 'movement' o 'static'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if capture_mode not in INGEST_ALLOWED_CAPTURE_MODES:
            return Response(
                {'error': f"Este endpoint solo acepta capture_mode "
                          f"{' / '.join(INGEST_ALLOWED_CAPTURE_MODES)}. Las medidas en movimiento "
                          f"se suben como track: /api/tracks/upload_json/."},
                status=status.HTTP_403_FORBIDDEN,
            )

        received = timezone.now()
        try:
            date_time = _parse_ingest_datetime(
                _ingest_first(data, 'dateTime', 'datetime', 'timestamp', 'ts'),
                fallback=received,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        station = None
        if capture_mode == 'static':
            station = getattr(device, 'station', None)
            if station is None:
                return Response(
                    {'error': 'Este dispositivo no tiene una estación asociada; no puede enviar medidas static.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            project = station.project
            latitude, longitude = station.latitude, station.longitude
            altitude = station.altitude
        else:
            project_id = _ingest_first(data, 'project', 'project_id')
            if project_id is None:
                return Response(
                    {'error': 'Falta el proyecto (project) para una medida movement.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                project = Project.objects.get(pk=project_id)
            except (Project.DoesNotExist, ValueError, TypeError):
                return Response(
                    {'error': f'Proyecto {project_id} no encontrado.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not project.is_active:
                return Response({'error': 'El proyecto no está activo.'}, status=status.HTTP_400_BAD_REQUEST)
            if project.project_type != 'radiation':
                return Response({'error': 'El proyecto no es de radiación.'}, status=status.HTTP_400_BAD_REQUEST)

            latitude = _ingest_first(data, 'latitude', 'lat')
            longitude = _ingest_first(data, 'longitude', 'lon', 'lng')
            altitude = _ingest_first(data, 'altitude', 'alt')
            if latitude is None or longitude is None:
                return Response(
                    {'error': 'Faltan latitude/longitude para una medida movement.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Idempotent dedup: same device + same instant -> return the existing one.
        existing = RadiationMeasurement.objects.filter(device=device, dateTime=date_time).first()
        if existing is not None:
            return Response(
                {'id': existing.id, 'duplicate': True, 'capture_mode': existing.capture_mode},
                status=status.HTTP_200_OK,
            )

        dose_rate = _ingest_first(data, 'dose_rate', 'doseMicroSvPerHour', 'dose')
        cpm = _ingest_first(data, 'cpm')
        # La app envía errores relativos (fracción); aceptamos también los
        # absolutos antiguos (cpm_error/cpmErr) por compatibilidad.
        cpm_rel_error = _ingest_first(data, 'cpm_rel_error', 'cpmRelErr')
        dose_rate_rel_error = _ingest_first(data, 'dose_rate_rel_error', 'doseMicroSvPerHourRelErr')
        cpm_error = _ingest_first(data, 'cpm_error', 'cpmErr')
        dose_rate_error = _ingest_first(data, 'dose_rate_error', 'doseMicroSvPerHourErr')
        cpm_f = float(cpm) if cpm is not None else None
        dose_rate_f = float(dose_rate) if dose_rate is not None else None
        cpm_rel_error_f = float(cpm_rel_error) if cpm_rel_error is not None else None
        dose_rate_rel_error_f = float(dose_rate_rel_error) if dose_rate_rel_error is not None else None
        cpm_error_f = float(cpm_error) if cpm_error is not None else None
        if cpm_error_f is None and cpm_f is not None and cpm_rel_error_f is not None:
            cpm_error_f = cpm_f * cpm_rel_error_f
        dose_rate_error_f = float(dose_rate_error) if dose_rate_error is not None else None
        if dose_rate_error_f is None and dose_rate_f is not None and dose_rate_rel_error_f is not None:
            dose_rate_error_f = dose_rate_f * dose_rate_rel_error_f
        try:
            measurement = RadiationMeasurement(
                device=device,
                user=device.owner,
                project=project,
                campaign=None,  # ingest never sets a campaign (mission therefore None)
                station=station,
                capture_mode=capture_mode,
                dateTime=date_time,
                received_at=received,
                latitude=latitude,
                longitude=longitude,
                altitude=altitude,
                cpm=int(cpm_f) if cpm_f is not None else None,
                cpm_error=cpm_error_f,
                cpm_rel_error=cpm_rel_error_f,
                dose_rate=dose_rate_f,
                dose_rate_error=dose_rate_error_f,
                dose_rate_rel_error=dose_rate_rel_error_f,
                speed=_ingest_first(data, 'speed'),
                accuracy=_ingest_first(data, 'accuracy', 'accuracyMeters'),
            )
            # Claiming the slot also bumps the watchdog state (last_measurement_at,
            # status), so a station that reports keeps itself online. Inside the
            # transaction: if the reading turns out to be unsaveable, the claim is
            # rolled back with it and the next one is not turned away for nothing.
            #
            # Weather is NOT handled here — the periodic
            # assign_pending_weather_buckets sweeper buckets every measurement
            # (track / movement / static) that lacks a weather_cache, so ingest
            # stays free of weather logic.
            with transaction.atomic():
                if station is not None:
                    claimed, retry_after = _claim_station_slot(station)
                    if not claimed:
                        logger.warning(
                            f"ingest: estación {station.id} ({station.name}) enviando por encima "
                            f"de su cadencia ({station.expected_interval_seconds}s); rechazada."
                        )
                        response = Response(
                            {
                                'error': 'Demasiado pronto: esta estación ya ha enviado una medida '
                                         'dentro de su intervalo.',
                                'retry_after': retry_after,
                                'expected_interval_seconds': station.expected_interval_seconds,
                            },
                            status=status.HTTP_429_TOO_MANY_REQUESTS,
                        )
                        response['Retry-After'] = str(retry_after)
                        return response

                measurement.save()
        except (ValueError, TypeError) as exc:
            return Response({'error': f'Datos inválidos: {exc}'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                'id': measurement.id,
                'capture_mode': capture_mode,
                'project': project.id,
                'station': station.id if station else None,
                'dateTime': date_time,
                'received_at': received,
                'duplicate': False,
            },
            status=status.HTTP_201_CREATED,
        )