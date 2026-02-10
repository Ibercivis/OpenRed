"""
Statistics ViewSet for aggregated data endpoints.

This module provides read-only endpoints for statistics and aggregations:
- Global statistics (total measurements, date ranges, etc.)
- Heatmap data with H3 aggregation
- Project-level statistics
- Timeline aggregations

All endpoints are public (no authentication required) and optimized for caching.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.db.models import Count, Avg, Max, Min, Q, Sum
from django.db import connection
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
import h3
import logging
from datetime import datetime

from .models import RadiationMeasurement, LightPollutionMeasurement, Track
from missions.models import Project
from django.contrib.auth.models import User
from datetime import timedelta

logger = logging.getLogger(__name__)


class StatsViewSet(viewsets.ViewSet):
    """
    ViewSet for statistics and aggregated data endpoints.
    
    Provides public read-only access to:
    - Global system statistics
    - Heatmap data with H3 spatial aggregation
    - Timeline aggregations
    
    All endpoints are public and optimized for caching.
    """
    permission_classes = [AllowAny]
    
    @swagger_auto_schema(
        tags=['Statistics'],
        operation_description="""
        Get global statistics for measurements.
        
        Filter by measurement type (radiation or light_pollution) to get specific statistics.
        Returns comprehensive statistics including counts, date ranges, geographic coverage,
        activity metrics, and records.
        """,
        manual_parameters=[
            openapi.Parameter(
                'type',
                openapi.IN_QUERY,
                description="Measurement type: 'radiation' or 'light_pollution'. Default: radiation",
                type=openapi.TYPE_STRING,
                enum=['radiation', 'light_pollution'],
                default='radiation'
            )
        ],
        responses={
            200: openapi.Response(
                description="Global statistics",
                examples={
                    "application/json": {
                        "measurement_type": "radiation",
                        "totals": {
                            "measurements": 98234,
                            "tracks": 342,
                            "projects": 15,
                            "contributors": 87
                        },
                        "date_range": {
                            "first": "2023-01-15T10:30:00Z",
                            "last": "2025-11-08T14:22:00Z",
                            "days_active": 892
                        },
                        "geographic": {
                            "bbox": {
                                "min_lat": 35.2,
                                "max_lat": 43.8,
                                "min_lon": -9.5,
                                "max_lon": 4.3
                            }
                        },
                        "distance": {
                            "total_km": 45823.5,
                            "longest_track_km": 456.7
                        },
                        "records": {
                            "highest_dose_rate": 256.8,
                            "lowest_dose_rate": 12.3,
                            "most_measurements_track": 8234
                        },
                        "fun_facts": {
                            "earth_circumferences": 1.15,
                            "marathon_equivalents": 1081
                        }
                    }
                }
            )
        }
    )
    @action(detail=False, methods=['get'], url_path='global')
    def global_stats(self, request):
        """
        GET /api/stats/global/?type=radiation
        
        Returns global statistics filtered by measurement type.
        """
        import time
        from django.db.models.functions import Coalesce
        from django.utils import timezone
        
        start_time = time.time()
        
        # Get measurement type parameter
        measurement_type = request.query_params.get('type', 'radiation')
        
        if measurement_type not in ['radiation', 'light_pollution']:
            return Response(
                {"error": "Invalid type. Use 'radiation' or 'light_pollution'"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Select appropriate model
        if measurement_type == 'radiation':
            MeasurementModel = RadiationMeasurement
            value_field = 'dose_rate'
        else:
            MeasurementModel = LightPollutionMeasurement
            value_field = 'lux'
        
        # Count measurements
        total_measurements = MeasurementModel.objects.count()
        
        # Get tracks that have this type of measurement
        if measurement_type == 'radiation':
            track_ids = MeasurementModel.objects.values_list('track_id', flat=True).distinct()
        else:
            track_ids = MeasurementModel.objects.values_list('track_id', flat=True).distinct()
        
        total_tracks = Track.objects.filter(id__in=track_ids, status='completed').count()
        
        # Count projects
        project_ids = MeasurementModel.objects.values_list('project_id', flat=True).distinct()
        total_projects = Project.objects.filter(id__in=project_ids).count()
        
        # Count contributors (anonymized)
        total_contributors = MeasurementModel.objects.values('user_id').distinct().count()
        
        # Date range
        date_stats = MeasurementModel.objects.aggregate(
            first=Min('dateTime'),
            last=Max('dateTime')
        )
        
        first_date = date_stats['first']
        last_date = date_stats['last']
        days_active = (last_date - first_date).days if (first_date and last_date) else 0
        
        date_range = {
            "first": first_date.isoformat() if first_date else None,
            "last": last_date.isoformat() if last_date else None,
            "days_active": days_active
        }
        
        # Geographic coverage (bounding box)
        bbox_stats = MeasurementModel.objects.aggregate(
            min_lat=Min('latitude'),
            max_lat=Max('latitude'),
            min_lon=Min('longitude'),
            max_lon=Max('longitude')
        )
        
        bbox = {
            "min_lat": float(bbox_stats['min_lat']) if bbox_stats['min_lat'] else None,
            "max_lat": float(bbox_stats['max_lat']) if bbox_stats['max_lat'] else None,
            "min_lon": float(bbox_stats['min_lon']) if bbox_stats['min_lon'] else None,
            "max_lon": float(bbox_stats['max_lon']) if bbox_stats['max_lon'] else None
        }
        
        # Distance metrics (from tracks)
        distance_stats = Track.objects.filter(
            id__in=track_ids,
            total_distance__isnull=False,
            status='completed'
        ).aggregate(
            total_meters=Coalesce(Sum('total_distance'), 0.0),
            max_meters=Coalesce(Max('total_distance'), 0.0)
        )
        
        total_km = distance_stats['total_meters'] / 1000.0
        max_km = distance_stats['max_meters'] / 1000.0
        
        # Records
        value_stats = MeasurementModel.objects.aggregate(
            max_value=Max(value_field),
            min_value=Min(value_field)
        )
        
        max_measurements = Track.objects.filter(
            id__in=track_ids,
            status='completed'
        ).aggregate(max=Max('total_measurements'))['max'] or 0
        
        # Build records based on type
        if measurement_type == 'radiation':
            records = {
                "highest_dose_rate": round(value_stats['max_value'], 2) if value_stats['max_value'] else None,
                "lowest_dose_rate": round(value_stats['min_value'], 2) if value_stats['min_value'] else None,
                "most_measurements_track": max_measurements
            }
        else:
            records = {
                "darkest_sky": round(value_stats['min_value'], 2) if value_stats['min_value'] else None,
                "brightest_sky": round(value_stats['max_value'], 2) if value_stats['max_value'] else None,
                "most_measurements_track": max_measurements
            }
        
        # Fun comparisons
        earth_circumferences = total_km / 40075.0 if total_km > 0 else 0
        marathon_equivalents = total_km / 42.195 if total_km > 0 else 0
        
        # Build response
        response_data = {
            "measurement_type": measurement_type,
            "totals": {
                "measurements": total_measurements,
                "tracks": total_tracks,
                "projects": total_projects,
                "contributors": total_contributors
            },
            "date_range": date_range,
            "geographic": {
                "bbox": bbox
            },
            "distance": {
                "total_km": round(total_km, 2),
                "longest_track_km": round(max_km, 2)
            },
            "records": records,
            "fun_facts": {
                "earth_circumferences": round(earth_circumferences, 2),
                "marathon_equivalents": round(marathon_equivalents, 0)
            }
        }
        
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(f"📊 GET /api/stats/global/ | Time: {elapsed_ms:.2f}ms")
        
        return Response(response_data)
    
    @swagger_auto_schema(
        tags=['Statistics'],
        operation_description="""
        Get heatmap data with H3 spatial aggregation.
        
        Returns measurements aggregated by H3 hexagonal cells with statistics:
        - Count of measurements per cell
        - Average dose rate (radiation, μSv/h)
        - Average sky brightness (light pollution)
        - Cell center coordinates
        
        Supports filtering by:
        - Bounding box (bbox)
        - Project ID
        - Date range
        - Measurement type
        """,
        manual_parameters=[
            openapi.Parameter(
                'resolution',
                openapi.IN_QUERY,
                description="H3 resolution (0-15). Default: 8 (~5.5km cells). Lower = larger cells.",
                type=openapi.TYPE_INTEGER,
                default=8
            ),
            openapi.Parameter(
                'bbox',
                openapi.IN_QUERY,
                description="Bounding box: min_lon,min_lat,max_lon,max_lat (e.g., -9.5,35.2,4.3,43.8)",
                type=openapi.TYPE_STRING
            ),
            openapi.Parameter(
                'project',
                openapi.IN_QUERY,
                description="Filter by project ID",
                type=openapi.TYPE_INTEGER
            ),
            openapi.Parameter(
                'start_date',
                openapi.IN_QUERY,
                description="Filter measurements from this date (ISO 8601: YYYY-MM-DD)",
                type=openapi.TYPE_STRING
            ),
            openapi.Parameter(
                'end_date',
                openapi.IN_QUERY,
                description="Filter measurements until this date (ISO 8601: YYYY-MM-DD)",
                type=openapi.TYPE_STRING
            ),
            openapi.Parameter(
                'type',
                openapi.IN_QUERY,
                description="Measurement type: 'radiation' or 'light_pollution'. Default: both",
                type=openapi.TYPE_STRING,
                enum=['radiation', 'light_pollution']
            )
        ],
        responses={
            200: openapi.Response(
                description="Heatmap data with H3 aggregation",
                examples={
                    "application/json": {
                        "resolution": 8,
                        "cell_count": 145,
                        "total_measurements": 12543,
                        "cells": [
                            {
                                "h3": "88754e64ddbffff",
                                "count": 1255,
                                "avg_dose_rate": 0.12,
                                "avg_lux": 19.1,
                                "center": {"lat": 40.4168, "lon": -3.7038}
                            }
                        ]
                    }
                }
            ),
            400: "Invalid parameters"
        }
    )
    @action(detail=False, methods=['get'], url_path='heatmap')
    def heatmap(self, request):
        """
        GET /api/stats/heatmap/?resolution=8&bbox=...&project=...
        
        Returns H3 aggregated heatmap data for visualization.
        """
        import time
        start_time = time.time()
        
        # Parse parameters
        resolution = int(request.query_params.get('resolution', 8))
        if not (0 <= resolution <= 15):
            return Response(
                {"error": "Resolution must be between 0 and 15"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        bbox_param = request.query_params.get('bbox')
        project_id = request.query_params.get('project')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        measurement_type = request.query_params.get('type')
        
        # Build querysets with filters
        radiation_qs = RadiationMeasurement.objects.all()
        light_pollution_qs = LightPollutionMeasurement.objects.all()
        
        # Apply measurement type filter
        if measurement_type == 'radiation':
            light_pollution_qs = light_pollution_qs.none()
        elif measurement_type == 'light_pollution':
            radiation_qs = radiation_qs.none()
        
        # Apply bbox filter
        if bbox_param:
            try:
                min_lon, min_lat, max_lon, max_lat = map(float, bbox_param.split(','))
                radiation_qs = radiation_qs.filter(
                    latitude__gte=min_lat,
                    latitude__lte=max_lat,
                    longitude__gte=min_lon,
                    longitude__lte=max_lon
                )
                light_pollution_qs = light_pollution_qs.filter(
                    latitude__gte=min_lat,
                    latitude__lte=max_lat,
                    longitude__gte=min_lon,
                    longitude__lte=max_lon
                )
            except (ValueError, TypeError):
                return Response(
                    {"error": "Invalid bbox format. Use: min_lon,min_lat,max_lon,max_lat"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        if project_id:
            radiation_qs = radiation_qs.filter(project_id=project_id)
            light_pollution_qs = light_pollution_qs.filter(project_id=project_id)

        if start_date:
            radiation_qs = radiation_qs.filter(dateTime__gte=start_date)
            light_pollution_qs = light_pollution_qs.filter(dateTime__gte=start_date)
        if end_date:
            radiation_qs = radiation_qs.filter(dateTime__lte=end_date)
            light_pollution_qs = light_pollution_qs.filter(dateTime__lte=end_date)
        
        # Aggregate by H3 cell
        cells = {}
        
        # Process radiation measurements
        for measurement in radiation_qs.values('latitude', 'longitude', 'dose_rate'):
            try:
                h3_cell = h3.latlng_to_cell(
                    float(measurement['latitude']),
                    float(measurement['longitude']),
                    resolution
                )
                
                if h3_cell not in cells:
                    cells[h3_cell] = {
                        'count': 0,
                        'dose_rate_sum': 0,
                        'dose_rate_count': 0,
                        'lux_sum': 0,
                        'lux_count': 0
                    }

                cells[h3_cell]['count'] += 1
                if measurement['dose_rate'] is not None:
                    cells[h3_cell]['dose_rate_sum'] += float(measurement['dose_rate'])
                    cells[h3_cell]['dose_rate_count'] += 1
            except Exception as e:
                logger.warning(f"Error processing radiation measurement: {e}")
                continue
        
        # Process light pollution measurements
        for measurement in light_pollution_qs.values('latitude', 'longitude', 'lux'):
            try:
                h3_cell = h3.latlng_to_cell(
                    float(measurement['latitude']),
                    float(measurement['longitude']),
                    resolution
                )
                
                if h3_cell not in cells:
                    cells[h3_cell] = {
                        'count': 0,
                        'dose_rate_sum': 0,
                        'dose_rate_count': 0,
                        'lux_sum': 0,
                        'lux_count': 0
                    }
                
                cells[h3_cell]['count'] += 1
                if measurement['lux'] is not None:
                    cells[h3_cell]['lux_sum'] += float(measurement['lux'])
                    cells[h3_cell]['lux_count'] += 1
            except Exception as e:
                logger.warning(f"Error processing light pollution measurement: {e}")
                continue
        
        # Build response with cell data
        cell_data = []
        total_measurements = 0
        
        for h3_cell, data in cells.items():
            # Get cell center coordinates
            lat, lon = h3.cell_to_latlng(h3_cell)
            
            cell_info = {
                'h3': h3_cell,
                'count': data['count'],
                'center': {
                    'lat': round(lat, 6),
                    'lon': round(lon, 6)
                }
            }
            
            # Add average dose_rate if available
            if data['dose_rate_count'] > 0:
                cell_info['avg_dose_rate'] = round(data['dose_rate_sum'] / data['dose_rate_count'], 3)
            
            # Add average lux if available
            if data['lux_count'] > 0:
                cell_info['avg_lux'] = round(data['lux_sum'] / data['lux_count'], 2)
            
            cell_data.append(cell_info)
            total_measurements += data['count']
        
        # Sort by count (descending)
        cell_data.sort(key=lambda x: x['count'], reverse=True)
        
        response_data = {
            'resolution': resolution,
            'cell_count': len(cell_data),
            'total_measurements': total_measurements,
            'cells': cell_data
        }
        
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(f"📊 GET /api/stats/heatmap/ | "
                   f"Time: {elapsed_ms:.2f}ms | "
                   f"Resolution: {resolution} | "
                   f"Cells: {len(cell_data)} | "
                   f"Measurements: {total_measurements}")
        
        return Response(response_data)

