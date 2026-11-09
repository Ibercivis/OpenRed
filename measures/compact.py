"""
Compact, column-oriented reads of measurements for bulk clients.

The regular list() builds a model instance and runs the full DRF serializer for
every row, nested weather_cache included: ~1 KB and ~0.25 ms per row. The map
only needs coordinates and the value it colours by, and a track download only a
handful of fields, so this endpoint reads just the requested columns with
values_list() and returns them as arrays:

    {"fields": ["id", "lat", "lon", "dose_rate"],
     "rows": [[123, 41.649871, -0.879123, 0.12], ...],
     "count": 1, "truncated": false}

It goes through the viewset's filter_queryset(), so it accepts the same query
parameters as list() and returns the same rows.
"""
from contextlib import contextmanager

from django.db import connection, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.fields import DateTimeField
from rest_framework.response import Response

COMPACT_DEFAULT_LIMIT = 50_000
COMPACT_MAX_LIMIT = 200_000

_datetime_field = DateTimeField()


def _coord(value):
    return None if value is None else round(float(value), 6)


def _datetime(value):
    return None if value is None else _datetime_field.to_representation(value)


# Public name -> (model field, converter). Shared by both measurement types.
COMMON_COMPACT_FIELDS = {
    'id': ('id', None),
    'dateTime': ('dateTime', _datetime),
    'lat': ('latitude', _coord),
    'lon': ('longitude', _coord),
    'altitude': ('altitude', None),
    'accuracy': ('accuracy', None),
    'speed': ('speed', None),
    'track': ('track_id', None),
}

RADIATION_COMPACT_FIELDS = {
    **COMMON_COMPACT_FIELDS,
    'cpm': ('cpm', None),
    'cpm_error': ('cpm_error', None),
    'cpm_rel_error': ('cpm_rel_error', None),
    'dose_rate': ('dose_rate', None),
    'dose_rate_error': ('dose_rate_error', None),
    'dose_rate_rel_error': ('dose_rate_rel_error', None),
}

LIGHT_POLLUTION_COMPACT_FIELDS = {
    **COMMON_COMPACT_FIELDS,
    'lux': ('lux', None),
    'cct': ('cct', None),
}

_ORDERINGS = ('dateTime', '-dateTime')


@contextmanager
def jit_off():
    """
    Disable PostgreSQL JIT for the queries inside the block.

    These reads are short; the planner misestimates them and JIT compilation
    alone cost ~0.7 s on a 21k-row bbox query that runs in ~0.1 s without it.
    """
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL jit = off')
        yield


class CompactMeasurementsMixin:
    """
    Adds GET .../compact/ to a measurement viewset.

    Subclasses set ``compact_fields`` (allowed public names) and
    ``compact_default_fields`` (what the map gets without ?fields=).
    """
    compact_fields = COMMON_COMPACT_FIELDS
    compact_default_fields = ('id', 'lat', 'lon')

    @extend_schema(
        summary='Compact measurements (bulk reads)',
        description=(
            'Same filters as the list endpoint, but returns only the requested columns '
            'as arrays: {"fields": [...], "rows": [[...], ...], "count": N, "truncated": bool}. '
            'Use it instead of the list endpoint to read many measurements (map, tracks).'
        ),
        parameters=[
            OpenApiParameter('fields', OpenApiTypes.STR, description='Comma-separated column names. Default: the map columns.'),
            OpenApiParameter('ordering', OpenApiTypes.STR, enum=list(_ORDERINGS), description='Optional order. Unordered by default.'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Max rows (default {COMPACT_DEFAULT_LIMIT}, max {COMPACT_MAX_LIMIT}). "truncated" is true when more rows matched.'),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=False, methods=['get'], url_path='compact')
    def compact(self, request):
        params = request.query_params

        raw_fields = params.get('fields')
        if raw_fields:
            names = [f.strip() for f in raw_fields.split(',') if f.strip()]
        else:
            names = list(self.compact_default_fields)
        unknown = [n for n in names if n not in self.compact_fields]
        if unknown or not names:
            return Response(
                {'error': f'Unknown fields: {", ".join(unknown) or "(none)"}. '
                          f'Allowed: {", ".join(self.compact_fields)}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ordering = params.get('ordering')
        if ordering and ordering not in _ORDERINGS:
            return Response(
                {'error': f'Invalid ordering. Allowed: {", ".join(_ORDERINGS)}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            limit = int(params.get('limit', COMPACT_DEFAULT_LIMIT))
        except ValueError:
            return Response({'error': 'Invalid limit.'}, status=status.HTTP_400_BAD_REQUEST)
        limit = max(1, min(limit, COMPACT_MAX_LIMIT))

        columns = [self.compact_fields[n][0] for n in names]
        converters = [(i, conv) for i, n in enumerate(names) if (conv := self.compact_fields[n][1])]

        queryset = self.filter_queryset(self.get_queryset())
        queryset = queryset.order_by(ordering) if ordering else queryset.order_by()
        # select_related('weather_cache') from the base queryset is irrelevant here:
        # values_list() only joins what the columns need.
        with jit_off():
            rows = list(queryset.values_list(*columns)[:limit + 1])

        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]

        if converters:
            out = []
            for row in rows:
                row = list(row)
                for i, conv in converters:
                    row[i] = conv(row[i])
                out.append(row)
        else:
            out = [list(row) for row in rows]

        return Response({
            'fields': names,
            'rows': out,
            'count': len(out),
            'truncated': truncated,
        })
