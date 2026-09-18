"""
Capture-mode scoping for radiation queries.

A station is a fixed point emitting a time series: thousands of readings at the
very same coordinates. Mixed into a spatial view they swamp it — one hexagon
ends up with a measurement_count orders of magnitude above the rest, and it is
the only one that survives any min_count. So the spatial endpoints (points, H3,
histogram, heatmap, report) show movement data only.

Callers opt back in with ?capture_mode=static|all, or by asking for a single
station with ?station=<id>. Endpoints that report data volume are exempt: they
pass default=None so that the project total stays the real total, stations
included.

Everything that queries radiation measurements should go through here, whether
it builds a queryset or raw SQL, so the scope cannot be forgotten in one place
and applied in another.
"""
from .models import RadiationMeasurement


def requested_capture_mode(request, default='movement'):
    """
    Return the capture_mode a request is scoped to, or None for "every mode".

    ``default`` is what applies when the caller says nothing: 'movement' for the
    spatial endpoints, None for the counters.
    """
    mode = (request.query_params.get('capture_mode') or '').strip().lower()
    if mode in ('movement', 'static'):
        return mode
    if mode == 'all':
        return None
    if request.query_params.get('station'):
        # Asking for one station means asking for its series.
        return None
    return default


def scope_capture_mode_qs(request, queryset, default='movement'):
    """
    Apply the capture-mode scope to a radiation queryset.

    No-op on other models: only radiation measurements have a capture_mode.
    """
    if queryset.model is not RadiationMeasurement:
        return queryset

    station_id = request.query_params.get('station')
    if station_id:
        queryset = queryset.filter(station_id=station_id)

    mode = requested_capture_mode(request, default=default)
    if mode is not None:
        queryset = queryset.filter(capture_mode=mode)
    return queryset


def scope_capture_mode_sql(request, where_clauses, params, default='movement'):
    """Apply the capture-mode scope to a raw-SQL radiation query, in place."""
    mode = requested_capture_mode(request, default=default)
    if mode is not None:
        where_clauses.append("capture_mode = %(capture_mode)s")
        params['capture_mode'] = mode
