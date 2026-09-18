"""
Time-series bucketing for station readings.

A station is a fixed point reporting on a cadence, so its readings are a time
series and must be aggregated server-side before they are charted: at one
reading every 5 minutes a station produces ~105k rows a year, and no chart —
and no browser — wants them raw.

The shape follows what time-series APIs conventionally do (the ``step`` of
Prometheus, the ``aggregateWindow`` of InfluxDB): the caller gives a window and
an interval, the server returns one point per bucket with avg/min/max/count, and
``auto`` derives the interval from the window so the response stays in the low
hundreds of points regardless of the range asked for.
"""
import re

from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import timedelta
from rest_framework import status
from rest_framework.response import Response


#: Hard ceiling for ?interval=raw. Beyond this the caller wants an export, not a chart.
RAW_READINGS_CAP = 20000

#: Refuse to build a series longer than this: it would be a slow query answering
#: a question no chart can render.
MAX_POINTS = 5000

#: Bucket sizes. Minutes are 'm' here, as everywhere ('5m' = five minutes).
INTERVALS = {
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '1h': 3600,
    '6h': 21600,
    '1d': 86400,
    '1w': 604800,
}

#: Window units. These are the chart's period buttons — 1d, 1w, 1m, 1y, 4y — so
#: 'm' means MONTH, not minute: a window of minutes is not something anyone
#: charts here, whereas "the last month" is the third button. 'mo' is accepted
#: as the unambiguous spelling. Months and years are the usual approximations
#: (30 and 365 days): for the extent of a chart window nobody cares that
#: February is short.
RANGE_UNITS = {
    'h': 3600,
    'd': 86400,
    'w': 604800,
    'm': 30 * 86400,
    'mo': 30 * 86400,
    'y': 365 * 86400,
}

#: Window length -> bucket, so the point count stays in the hundreds:
#: 1d/5m = 288 points, 1w/1h = 168, 1m/1h = 720, 1y/1d = 365, 4y/1w = 209.
AUTO_INTERVALS = [
    (6 * 3600, 60),
    (48 * 3600, 300),
    (7 * 86400, 3600),
    (31 * 86400, 3600),
    (92 * 86400, 21600),
    (366 * 86400, 86400),
]
AUTO_FALLBACK = 604800


def _bad_request(message):
    return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)


def parse_time_window(request):
    """
    Resolve the requested window into (start, end) aware datetimes.

    ``start``/``end`` are ISO-8601 and win when present; otherwise ``range`` is a
    span back from now — the chart's period buttons: 1d, 1w, 1m, 1y, 4y (and any
    other N + unit) — defaulting to the last 24 hours.

    Returns ((start, end), None) or (None, error_response).
    """
    now = timezone.now()

    raw_start = request.query_params.get('start')
    raw_end = request.query_params.get('end')

    if raw_start or raw_end:
        start = parse_datetime(raw_start) if raw_start else None
        end = parse_datetime(raw_end) if raw_end else now
        if raw_start and start is None:
            return None, _bad_request('Invalid start: use ISO-8601 (e.g. 2026-07-11T00:00:00Z).')
        if raw_end and end is None:
            return None, _bad_request('Invalid end: use ISO-8601 (e.g. 2026-07-11T00:00:00Z).')
        if start is None:
            start = end - timedelta(hours=24)
        # A naive timestamp is read as UTC, matching how the series is returned.
        if timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.utc)
        if timezone.is_naive(end):
            end = timezone.make_aware(end, timezone.utc)
    else:
        raw_range = (request.query_params.get('range') or '24h').strip().lower()
        match = re.fullmatch(r'(\d+)(mo|[hdwmy])', raw_range)
        if not match:
            return None, _bad_request(
                "Invalid range: use N + h|d|w|m|y (e.g. 1d, 1w, 1m, 1y, 4y). 'm' is months."
            )
        seconds = int(match.group(1)) * RANGE_UNITS[match.group(2)]
        if seconds <= 0:
            return None, _bad_request('Invalid range: must be positive.')
        end = now
        start = end - timedelta(seconds=seconds)

    if start >= end:
        return None, _bad_request('Invalid window: start must be before end.')

    return (start, end), None


def resolve_interval(request, start, end):
    """
    Resolve the bucket size in seconds; None means "raw readings, no bucketing".

    Returns (interval, None) or (None, error_response).
    """
    raw = (request.query_params.get('interval') or 'auto').strip().lower()

    if raw == 'raw':
        return None, None

    if raw == 'auto':
        span = (end - start).total_seconds()
        for limit, interval in AUTO_INTERVALS:
            if span <= limit:
                return interval, None
        return AUTO_FALLBACK, None

    if raw not in INTERVALS:
        return None, _bad_request(
            f"Invalid interval: use auto, raw, or one of {', '.join(INTERVALS)}."
        )

    interval = INTERVALS[raw]
    points = (end - start).total_seconds() / interval
    if points > MAX_POINTS:
        return None, _bad_request(
            f'Interval {raw} over this window yields {int(points)} points (max {MAX_POINTS}). '
            f'Use a coarser interval or a shorter window.'
        )
    return interval, None


def interval_label(interval):
    """Seconds back to the label the caller used ('5m', '1h', ...)."""
    for label, seconds in INTERVALS.items():
        if seconds == interval:
            return label
    return f'{interval}s'


def bucketed_readings(station_id, start, end, interval):
    """
    One point per non-empty bucket: avg/min/max of both metrics, plus the number
    of readings behind it.

    Empty buckets are simply absent — see the endpoint docstring on why they are
    not zero-filled.
    """
    sql = """
        SELECT
            to_timestamp(floor(extract(epoch FROM "dateTime") / %(interval)s) * %(interval)s) AS bucket,
            COUNT(*)::int                AS count,
            AVG(dose_rate)::float        AS dose_rate_avg,
            MIN(dose_rate)::float        AS dose_rate_min,
            MAX(dose_rate)::float        AS dose_rate_max,
            AVG(cpm)::float              AS cpm_avg,
            MIN(cpm)::int                AS cpm_min,
            MAX(cpm)::int                AS cpm_max
        FROM measures_radiation_measurement
        WHERE station_id = %(station_id)s
          AND "dateTime" >= %(start)s
          AND "dateTime" <  %(end)s
        GROUP BY bucket
        ORDER BY bucket
    """
    params = {'station_id': station_id, 'start': start, 'end': end, 'interval': interval}

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return [
        {
            't': bucket,
            'count': count,
            'dose_rate': {
                'avg': round(dr_avg, 6) if dr_avg is not None else None,
                'min': dr_min,
                'max': dr_max,
            },
            'cpm': {
                'avg': round(cpm_avg, 2) if cpm_avg is not None else None,
                'min': cpm_min,
                'max': cpm_max,
            },
        }
        for bucket, count, dr_avg, dr_min, dr_max, cpm_avg, cpm_min, cpm_max in rows
    ]


def bucketed_weather(station_id, start, end, interval):
    """
    The weather behind the same window, as its own series.

    Weather is cached per (H3 cell, hour) and a station never moves, so one hour
    is its true resolution: it is returned hourly, or at the reading bucket when
    that is coarser (a daily chart wants daily weather). Sub-hourly buckets are
    NOT interpolated — repeating one hourly value across twelve 5-minute points
    would fake a resolution the data does not have.

    rain_sum adds up across the bucket (mm accumulated); the rest are averaged.
    wind_direction is only reported hourly: averaging degrees across a bucket is
    wrong (0° and 359° average to 180°, the opposite direction).
    """
    bucket_seconds = max(3600, interval or 3600)
    hourly = bucket_seconds == 3600

    sql = """
        SELECT
            to_timestamp(floor(extract(epoch FROM w.timestamp_hour) / %(bucket)s) * %(bucket)s) AS bucket,
            AVG(w.temperature)::float   AS temperature,
            AVG(w.humidity)::float      AS humidity,
            AVG(w.pressure)::float      AS pressure,
            AVG(w.wind_speed)::float    AS wind_speed,
            AVG(w.cloud_cover)::float   AS cloud_cover,
            SUM(w.rain_sum)::float      AS rain_sum,
            AVG(w.wind_direction)::float AS wind_direction,
            COUNT(*)::int               AS hours
        FROM measures_weathercache w
        WHERE w.fetched = TRUE
          AND w.id IN (
              SELECT DISTINCT weather_cache_id
              FROM measures_radiation_measurement
              WHERE station_id = %(station_id)s
                AND "dateTime" >= %(start)s
                AND "dateTime" <  %(end)s
                AND weather_cache_id IS NOT NULL
          )
        GROUP BY bucket
        ORDER BY bucket
    """
    params = {'station_id': station_id, 'start': start, 'end': end, 'bucket': bucket_seconds}

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    def _round(value, digits):
        return round(value, digits) if value is not None else None

    series = []
    for bucket, temp, humidity, pressure, wind_speed, cloud, rain, wind_dir, hours in rows:
        point = {
            't': bucket,
            'hours': hours,
            'temperature': _round(temp, 2),
            'humidity': _round(humidity, 1),
            'pressure': _round(pressure, 2),
            'wind_speed': _round(wind_speed, 2),
            'cloud_cover': _round(cloud, 1),
            'rain_sum': _round(rain, 2),
        }
        if hourly:
            point['wind_direction'] = _round(wind_dir, 1)
        series.append(point)
    return series
