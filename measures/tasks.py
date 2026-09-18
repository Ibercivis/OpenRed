"""
Background tasks for processing track files using RQ (Redis Queue).

This module contains asynchronous tasks for parsing uploaded track files
(CSV/GPX) and creating measurement records in batches. Tasks are executed
by RQ workers to avoid blocking HTTP requests.

Functions:
    - process_track_file(track_id): Main task for processing track uploads
    - parse_csv_track(track, file_content): Parse CSV format and create measurements
    - parse_gpx_track(track, file_content): Parse GPX format (not yet implemented)
    - fetch_weather_for_track(track_id): Fetch weather data for track measurements
    - fetch_weather_batch(caches): Batch fetch weather from Open-Meteo API

Task Workflow:
    1. Client uploads file → Track created with status='pending'
    2. Task enqueued to RQ worker
    3. Worker picks up task → status='processing'
    4. Parse file and bulk create measurements
    5. Update Track with counts and status='completed' (or 'failed' on error)
    6. Enqueue weather fetching task (async)
"""
import csv
import io
import json
import math
import logging
import statistics
import requests
import h3
from django.contrib.gis.geos import Point
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import datetime, timedelta, timezone as dt_timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


def process_track_file(track_id):
    """
    Process uploaded track file in background using RQ worker.
    
    This is the main background task that handles track file processing.
    It updates track status throughout the process and handles errors gracefully.
    
    Args:
        track_id (int): Primary key of the Track object to process
        
    Returns:
        dict: Processing result with keys:
            - success (bool): True if processing succeeded
            - track_id (int): ID of processed track
            - measurements_created (int): Number of measurements created (if success)
            - error (str): Error message (if not success)
            - status (str): Final track status ('completed' or 'failed')
    
    Processing Steps:
        1. Fetch Track object and set status to 'processing'
        2. Read file content from storage
        3. Call appropriate parser based on file_type (csv/gpx)
        4. Bulk create measurements in database
        5. Update Track with measurement count and status='completed'
        6. On error: Set status='failed' and store error message
    
    Example:
        >>> from django_rq import enqueue
        >>> enqueue(process_track_file, track_id=123)
        >>> # Worker will process asynchronously
        
        >>> # Check result later
        >>> track = Track.objects.get(id=123)
        >>> track.status  # 'completed', 'processing', or 'failed'
        >>> track.total_measurements  # Count of created measurements
    
    Raises:
        Track.DoesNotExist: If track_id doesn't exist (caught and returned in result)
        ValueError: If file type is unsupported (caught and returned in result)
        Exception: Any other error (caught, stored in track.error_message, returned)
    
    Note:
        This function is designed to be called by RQ workers, not directly.
        Use django_rq.enqueue() to queue this task.
    """
    # Import here to avoid circular imports
    from .models import Track, RadiationMeasurement
    
    try:
        track = Track.objects.get(id=track_id)
        track.status = 'processing'
        track.save(update_fields=['status'])
        
        # Open and read the file
        track.file.open('r')
        file_content = track.file.read()
        track.file.close()
        
        # Parse based on file type (only RCTRK is supported)
        if track.file_type == 'rctrk':
            measurements_count = parse_rctrk_track(track, file_content)
        elif track.file_type == 'json':
            measurements_count = parse_json_track(track, file_content)
        else:
            raise ValueError(f'Unsupported file type: {track.file_type}. Only RCTRK and JSON formats are supported.')
        
        # Update track status
        track.total_measurements = measurements_count
        track.status = 'completed'
        track.save(update_fields=['total_measurements', 'status'])
        
        # Promptly bucket this track's points via the SHARED sweeper (no bucketing
        # logic lives in the parsers anymore). The scheduled fetch_pending_weather
        # then fills the buckets. Enqueue is best-effort: if it fails, the periodic
        # sweeper still picks the measurements up on its next pass.
        try:
            import django_rq
            queue = django_rq.get_queue('openred-weather')
            weather_job = queue.enqueue(
                'measures.tasks.assign_pending_weather_buckets',
                limit=measurements_count + 50,
                job_timeout='30m',
                result_ttl=3600,
                job_id=f'weather_buckets_track_{track_id}_{timezone.now().timestamp()}'
            )
            print(f"✅ Weather bucketing enqueued: {weather_job.id} for track {track_id}")
        except Exception as weather_error:
            print(f"⚠️ Failed to enqueue weather bucketing for track {track_id}: {weather_error}")
            # Don't fail the whole process if weather queueing fails
        
        _enqueue_track_notification(track_id)
        
        return {
            'success': True,
            'track_id': track_id,
            'measurements_created': measurements_count,
            'status': 'completed'
        }
        
    except Track.DoesNotExist:
        return {
            'success': False,
            'error': f'Track {track_id} not found'
        }
    except Exception as e:
        # Update track with error
        try:
            track = Track.objects.get(id=track_id)
            track.status = 'failed'
            track.error_message = str(e)
            track.save(update_fields=['status', 'error_message'])
        except:
            pass
        
        _enqueue_track_notification(track_id)
        
        return {
            'success': False,
            'error': str(e),
            'track_id': track_id
        }


def _enqueue_track_notification(track_id):
    """
    Enqueue (best-effort) the email notification for a processed track.

    Runs as a separate job on the 'openred-tracks' queue so that an SES
    outage can never mark a track as failed nor block its processing.
    """
    try:
        import django_rq
        queue = django_rq.get_queue('openred-tracks')
        queue.enqueue(
            'measures.tasks.notify_track_processed',
            track_id,
            job_timeout='2m',
            result_ttl=3600,
            job_id=f'track_notify_{track_id}',
        )
    except Exception as notify_error:
        logger.warning(f"Failed to enqueue notification for track {track_id}: {notify_error}")


def notify_track_processed(track_id):
    """
    Send an email to settings.TRACK_UPLOAD_NOTIFY_EMAILS when a track has
    finished processing (status 'completed' or 'failed').

    Tracks belonging to projects listed in
    settings.TRACK_UPLOAD_NOTIFY_EXCLUDE_PROJECTS (e.g. the test project)
    are silently skipped.

    Returns:
        dict: {'sent': bool, 'reason': str|None, 'track_id': int}
    """
    from django.conf import settings
    from django.core.mail import send_mail
    from django.utils import timezone as tz
    from .models import Track

    recipients = getattr(settings, 'TRACK_UPLOAD_NOTIFY_EMAILS', [])
    if not recipients:
        return {'sent': False, 'reason': 'no recipients configured', 'track_id': track_id}

    try:
        track = (Track.objects
                 .select_related('project', 'campaign', 'mission', 'device',
                                 'device__device_model', 'created_by')
                 .get(id=track_id))
    except Track.DoesNotExist:
        return {'sent': False, 'reason': 'track not found', 'track_id': track_id}

    excluded = getattr(settings, 'TRACK_UPLOAD_NOTIFY_EXCLUDE_PROJECTS', [])
    if track.project and track.project.name in excluded:
        return {'sent': False, 'reason': f'project {track.project.name} excluded', 'track_id': track_id}

    user = track.created_by
    user_str = user_short = 'desconocido'
    if user:
        user_str = user_short = user.username
        if user.email:
            user_str += f' <{user.email}>'
        full_name = f'{user.first_name} {user.last_name}'.strip()
        if full_name:
            user_short = full_name
            user_str = f'{full_name} ({user_str})'

    device = track.device
    device_str = 'desconocido'
    if device:
        device_str = device.serial_number
        if getattr(device, 'device_model', None):
            device_str += f' ({device.device_model.name})'

    status_label = {'completed': 'COMPLETADO', 'failed': 'FALLIDO'}.get(track.status, track.status.upper())
    track_type = dict(Track.TRACK_TYPE_CHOICES).get(track.track_type, track.track_type)
    campaign_str = track.campaign.name if track.campaign else '-'
    mission_str = track.mission.name if track.mission else '-'
    project_str = track.project.name if track.project else '-'
    created_local = tz.localtime(track.created_at).strftime('%Y-%m-%d %H:%M') if track.created_at else '-'

    subject = f'[OpenRed] Track #{track.id} {status_label}: {user_short} - {campaign_str}'

    lines = [
        f'Se ha procesado un track en OpenRed con estado {status_label}.',
        '',
        f'Track ID:      {track.id}',
        f'Estado:        {status_label}',
        f'Usuario:       {user_str}',
        f'Proyecto:      {project_str}',
        f'Campaña:       {campaign_str}',
        f'Misión:        {mission_str}',
        f'Dispositivo:   {device_str}',
        f'Tipo:          {track_type}',
        f'Fichero:       {track.file.name if track.file else "-"} ({track.file_type})',
        f'Medidas:       {track.total_measurements}',
        f'Subido:        {created_local}',
    ]
    if track.status == 'failed':
        lines += ['', 'Error:', track.error_message or '(sin mensaje)']
    lines += ['', 'Admin: https://api.open-red.es/admin/measures/track/%d/change/' % track.id]

    try:
        send_mail(
            subject=subject,
            message='\n'.join(lines),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=list(recipients),
            fail_silently=False,
        )
    except Exception as mail_error:
        logger.error(f"Failed to send track notification for track {track_id}: {mail_error}")
        return {'sent': False, 'reason': str(mail_error), 'track_id': track_id}

    logger.info(f"Track notification sent for track {track_id} to {recipients}")
    return {'sent': True, 'reason': None, 'track_id': track_id}


def parse_csv_track(track, file_content):
    """
    Parse CSV file and create RadiationMeasurement objects in bulk.
    
    Reads CSV data, validates rows, and creates measurement records
    using Django's bulk_create for performance. Handles various timestamp
    formats and missing/optional fields.
    
    Args:
        track (Track): Track instance that owns these measurements
        file_content (bytes | str): CSV file content to parse
        
    Returns:
        int: Number of measurements successfully created
    
    CSV Format:
        Required columns:
            - timestamp/dateTime/datetime: ISO 8601 format or compatible
            - latitude: Decimal degrees (-90 to 90)
            - longitude: Decimal degrees (-180 to 180)
            - dose_rate: Numeric dose rate reading
        
        Optional columns:
            - cpm: Counts per minute (integer)
            - altitude: Meters above sea level (float)
            - accuracy: GPS accuracy in meters (float)
    
    Processing:
        1. Decode bytes to UTF-8 string if needed
        2. Parse CSV with DictReader (first row = headers)
        3. For each row:
           - Parse timestamp (make timezone-aware if naive)
           - Extract required fields (lat, lon, radiation)
           - Extract optional fields (cpm, altitude, accuracy)
           - Create RadiationMeasurement instance (not saved)
        4. Bulk create all measurements (batch_size=1000)
        5. Update Track start_time/end_time from timestamps
    
    Example CSV:
        ```
        timestamp,latitude,longitude,dose_rate,cpm,altitude
        2024-11-20T14:30:00Z,40.4168,-3.7038,0.125,25,667
        2024-11-20T14:31:00Z,40.4170,-3.7040,0.120,24,668
        ```
    
    Raises:
        ValueError: If no valid measurements found in CSV
        KeyError: If required column is missing
        ValueError: If numeric field cannot be parsed
    
    Performance:
        Uses bulk_create with batch_size=1000 for efficiency.
        10,000 rows takes ~2-3 seconds vs ~60 seconds with individual saves.
    
    Note:
        Timestamps are made timezone-aware using Django's default timezone
        if they are naive (no timezone info). This ensures consistency
        with database storage.
    """
    from .models import RadiationMeasurement
    
    # Decode if bytes
    if isinstance(file_content, bytes):
        file_content = file_content.decode('utf-8')
    
    # Parse CSV
    csv_reader = csv.DictReader(io.StringIO(file_content))
    
    measurements = []
    timestamps = []
    
    for row in csv_reader:
        # Parse timestamp
        timestamp_str = row.get('timestamp') or row.get('dateTime') or row.get('datetime')
        if timestamp_str:
            dt = parse_datetime(timestamp_str)
            if dt and timezone.is_naive(dt):
                # Make naive datetime aware using default timezone
                dt = timezone.make_aware(dt)
            elif not dt:
                # Try manual parsing if parse_datetime failed
                try:
                    dt = datetime.fromisoformat(timestamp_str)
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt)
                except:
                    dt = timezone.now()
        else:
            dt = timezone.now()
        
        timestamps.append(dt)
        
        # Create measurement object (not saved yet)
        lat = float(row.get('latitude', 0))
        lon = float(row.get('longitude', 0))
        measurement = RadiationMeasurement(
            user=track.created_by,
            device=track.device,
            project=track.project,
            track=track,
            latitude=lat,
            longitude=lon,
            location=Point(lon, lat),
            cpm=int(row.get('cpm', 0)) if row.get('cpm') else None,
            dose_rate=float(row.get('dose_rate', 0)),
            altitude=float(row.get('altitude')) if row.get('altitude') else None,
            accuracy=float(row.get('accuracy')) if row.get('accuracy') else None,
            dateTime=dt
        )
        measurements.append(measurement)
    
    if not measurements:
        raise ValueError("No valid measurements found in CSV file")
    
    # Bulk create for efficiency with batching
    RadiationMeasurement.objects.bulk_create(measurements, batch_size=1000)
    
    # Update track start/end times
    track.start_time = min(timestamps)
    track.end_time = max(timestamps)
    track.save(update_fields=['start_time', 'end_time'])
    
    return len(measurements)


def detect_rctrk_format(file_content):
    """
    Detect which RadiaCode export format a .rctrk file uses.

    The RadiaCode app exports different formats depending on the platform:
        - Android: tab-separated text ("Track: ..." header line + column header + rows)
        - iOS: a JSON document ({"title", "devices", "start", "periods", "markers": [...]})

    Args:
        file_content (bytes | str): Raw file content

    Returns:
        str: 'ios' if the content is a JSON object, 'android' otherwise
    """
    if isinstance(file_content, bytes):
        file_content = file_content.decode('utf-8', errors='replace')
    stripped = file_content.lstrip('\ufeff \t\r\n')
    return 'ios' if stripped.startswith('{') else 'android'


# Column names used by the RadiaCode Android export, lower-cased. The app has
# changed the layout over time (2026 builds add Altitude and Temperature, and
# not always in the same position), so rows are mapped by header name.
_RCTRK_COLUMN_ALIASES = {
    'time': 'time',
    'latitude': 'latitude',
    'longitude': 'longitude',
    'accuracy': 'accuracy',
    'altitude': 'altitude',
    'doserate': 'dose_rate',
    'countrate': 'count_rate',
}
_RCTRK_REQUIRED_COLUMNS = ('time', 'latitude', 'longitude', 'dose_rate', 'count_rate')
# Layout of the original (2025) export, used only when the header line is unusable.
_RCTRK_LEGACY_COLUMNS = {'time': 1, 'latitude': 2, 'longitude': 3, 'accuracy': 4, 'dose_rate': 5, 'count_rate': 6}


def _rctrk_column_map(header_line):
    """
    Build {field: column_index} from the tab-separated header line of an Android export.

    Falls back to the legacy positional layout when the header does not contain
    the required column names.
    """
    columns = {}
    for idx, name in enumerate(header_line.split('\t')):
        key = _RCTRK_COLUMN_ALIASES.get(name.strip().lower())
        if key and key not in columns:
            columns[key] = idx
    if all(k in columns for k in _RCTRK_REQUIRED_COLUMNS):
        return columns
    return dict(_RCTRK_LEGACY_COLUMNS)


def _parse_rctrk_android_points(file_content):
    """
    Parse the Android RadiaCode export (tab-separated text) into a list of point dicts.

    Format:
        Line 1: Track: <name>\t<device>\t<comment>\tEC
        Line 2: Column headers (tab-separated)
        Line 3+: Data rows (tab-separated)

    Known header layouts (columns are mapped by name, so order does not matter):
        2025:  Timestamp Time Latitude Longitude Accuracy DoseRate CountRate Comment
        2026a: Timestamp Time Latitude Longitude Altitude Accuracy Temperature DoseRate CountRate Comment
        2026b: Timestamp Time Latitude Longitude Altitude DoseRate CountRate Comment
        2026c: Timestamp Time Latitude Longitude Altitude DoseRate CountRate Comment Accuracy Temperature

    Example:
        Track: 2025-08-27vistabella-ruta\tRC-102-008858\t \tEC
        Timestamp\tTime\tLatitude\tLongitude\tAccuracy\tDoseRate\tCountRate\tComment
        134007603713620000\t2025-08-27 09:26:11\t41.2184571\t-1.1548868\t1.94\t6.52\t7.47\t 

    Field mapping:
        - Timestamp: Windows FILETIME (not used)
        - Time: UTC datetime "YYYY-MM-DD HH:MM:SS" (used for dateTime)
        - Latitude / Longitude: decimal degrees
        - Accuracy: GPS accuracy in metres (optional, None when the column is absent)
        - Altitude: metres (optional, None when the column is absent)
        - DoseRate: μR/h → stored as μSv/h (value / 100)
        - CountRate: CPS → stored as CPM (value * 60)
        - Temperature / Comment: ignored

    Returns:
        list[dict]: one dict per valid row with keys
            dateTime, latitude, longitude, accuracy, altitude, dose_rate, cpm
    """
    lines = file_content.strip().split('\n')

    if len(lines) < 3:
        raise ValueError("Invalid RCTRK file: too few lines")

    columns = _rctrk_column_map(lines[1])
    min_columns = max(columns[k] for k in _RCTRK_REQUIRED_COLUMNS) + 1

    def cell(parts, key):
        idx = columns.get(key)
        if idx is None or idx >= len(parts):
            return ''
        return parts[idx].strip()

    def optional_float(parts, key):
        value = cell(parts, key)
        return float(value) if value else None

    points = []
    # Skip line 1 (Track info) and line 2 (headers)
    for line in lines[2:]:
        if not line.strip():
            continue

        parts = line.split('\t')
        if len(parts) < min_columns:
            continue  # Skip invalid rows

        try:
            # The Timestamp field uses a custom format, so we use Time instead
            time_str = cell(parts, 'time')  # "2025-08-27 09:26:11"
            try:
                dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
            except ValueError:
                dt = parse_datetime(time_str)
                if dt and timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
                elif not dt:
                    dt = timezone.now()

            latitude = float(cell(parts, 'latitude'))
            longitude = float(cell(parts, 'longitude'))
            accuracy = optional_float(parts, 'accuracy')
            altitude = optional_float(parts, 'altitude')
            dose_rate = float(cell(parts, 'dose_rate')) / 100  # μR/h → μSv/h
            cpm = int(float(cell(parts, 'count_rate')) * 60)  # CPS → CPM

            points.append({
                'dateTime': dt,
                'latitude': latitude,
                'longitude': longitude,
                'accuracy': accuracy,
                'altitude': altitude,
                'dose_rate': dose_rate,
                'cpm': cpm,
            })
        except (ValueError, IndexError):
            # Skip rows with parsing errors
            continue

    return points


def _parse_rctrk_ios_points(file_content):
    """
    Parse the iOS RadiaCode export (a .rctrk that is actually JSON) into point dicts.

    Format:
        {
            "title": "Track 16 Nov 2025 08:33:42",
            "devices": ["RC-102-008406"],
            "start": 1763278422,
            "periods": [{"distance": 6004.12, "start": 1763278422, "end": 1763287996}],
            "sv": false,
            "markers": [
                {"date": 1763283709, "lat": 41.816321, "lon": -1.822395,
                 "acc": 5, "doseRate": 5.73, "countRate": 5.38},
                ...
            ]
        }

    Field mapping (same units as the Android export):
        - date: Unix epoch seconds (UTC)
        - lat / lon: decimal degrees
        - acc: GPS accuracy in metres
        - doseRate: μR/h → stored as μSv/h (value / 100)
        - countRate: CPS → stored as CPM (value * 60)

    Returns:
        list[dict]: one dict per valid marker with keys
            dateTime, latitude, longitude, accuracy, altitude, dose_rate, cpm
    """
    try:
        data = json.loads(file_content)
    except ValueError as e:
        raise ValueError(f"Invalid iOS RCTRK file: not valid JSON ({e})")

    if not isinstance(data, dict) or not isinstance(data.get('markers'), list):
        raise ValueError("Invalid iOS RCTRK file: 'markers' list not found")

    points = []
    for marker in data['markers']:
        if not isinstance(marker, dict):
            continue
        try:
            dt = datetime.fromtimestamp(float(marker['date']), tz=dt_timezone.utc)
            latitude = float(marker['lat'])
            longitude = float(marker['lon'])
            dose_rate = float(marker['doseRate']) / 100  # μR/h → μSv/h
            cpm = int(float(marker['countRate']) * 60)  # CPS → CPM
            acc = marker.get('acc')
            accuracy = float(acc) if acc is not None else None
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            # Skip markers with missing or malformed fields
            continue

        points.append({
            'dateTime': dt,
            'latitude': latitude,
            'longitude': longitude,
            'accuracy': accuracy,
            'altitude': None,  # not present in the iOS export
            'dose_rate': dose_rate,
            'cpm': cpm,
        })

    return points


def parse_rctrk_track(track, file_content):
    """
    Parse an RCTRK file (RadiaCode export) and create RadiationMeasurement objects in bulk.

    Both platform-specific exports are supported and detected automatically
    (see detect_rctrk_format):
        - Android: tab-separated text  → _parse_rctrk_android_points
        - iOS: JSON document           → _parse_rctrk_ios_points

    Args:
        track (Track): Track instance that owns these measurements
        file_content (bytes | str): RCTRK file content to parse

    Returns:
        int: Number of measurements successfully created

    Post-processing (common to both formats):
        - Speed: moving-window estimate from GPS points with good accuracy (m/s)
        - Track metadata: start/end time, total distance, average speed, dose stats

    Raises:
        ValueError: If no valid measurements found or invalid format
    """
    from .models import RadiationMeasurement
    from math import radians, sin, cos, sqrt, atan2
    
    def haversine_distance(lat1, lon1, lat2, lon2):
        """
        Calculate distance between two GPS coordinates using Haversine formula.
        Returns distance in meters.
        """
        R = 6371000  # Earth radius in meters
        phi1, phi2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        
        a = sin(dphi/2)**2 + cos(phi1) * cos(phi2) * sin(dlambda/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return R * c
    
    # Decode if bytes
    if isinstance(file_content, bytes):
        file_content = file_content.decode('utf-8')
    
    rctrk_format = detect_rctrk_format(file_content)
    if rctrk_format == 'ios':
        points = _parse_rctrk_ios_points(file_content)
    else:
        points = _parse_rctrk_android_points(file_content)
    logger.info("Track %s: RCTRK format detected as %s, %d valid points", track.id, rctrk_format, len(points))
    
    from django.conf import settings
    max_points = getattr(settings, 'TRACK_UPLOAD_MAX_POINTS', 20000)
    if len(points) > max_points:
        raise ValueError(f"Too many points in RCTRK file: {len(points)} (maximum is {max_points})")
    
    # Speed / distance post-processing assumes chronological order. The iOS export
    # does not guarantee it (markers are stored unordered), so sort explicitly.
    points.sort(key=lambda p: p['dateTime'])
    
    # Build measurement objects (not saved yet, speed will be calculated later)
    # Inherit hierarchy from track: project, mission, campaign, user
    measurements = []
    timestamps = []
    for point in points:
        timestamps.append(point['dateTime'])
        measurements.append(RadiationMeasurement(
            user=track.created_by,
            device=track.device,
            project=track.project,
            campaign=track.campaign,
            track=track,
            latitude=point['latitude'],
            longitude=point['longitude'],
            location=Point(point['longitude'], point['latitude']),
            accuracy=point['accuracy'],
            altitude=point.get('altitude'),
            dose_rate=point['dose_rate'],
            radiation_unit="μSv/h",
            cpm=point['cpm'],
            speed=None,  # Will be calculated after all measurements are created
            dateTime=point['dateTime']
        ))
    
    if not measurements:
        raise ValueError("No valid measurements found in RCTRK file")
    
    # Bulk create for efficiency with batching
    RadiationMeasurement.objects.bulk_create(measurements, batch_size=1000)
    
    # Calculate speed using moving window (3 points before + current + 3 points after)
    # This smooths out GPS errors and gives more reliable speed estimates
    WINDOW_SIZE = 3  # points before and after
    MAX_ACCURACY_FOR_SPEED = 15.0  # meters - only use points with good GPS accuracy
    
    for i, measurement in enumerate(measurements):
        # Skip if current point has poor GPS accuracy
        if measurement.accuracy is None or measurement.accuracy > MAX_ACCURACY_FOR_SPEED:
            continue
            
        # Define window boundaries
        start_idx = max(0, i - WINDOW_SIZE)
        end_idx = min(len(measurements), i + WINDOW_SIZE + 1)
        
        # Get points in window with good accuracy
        window_points = []
        for j in range(start_idx, end_idx):
            m = measurements[j]
            if m.accuracy is not None and m.accuracy < MAX_ACCURACY_FOR_SPEED:
                window_points.append(m)
        
        # Need at least 2 points to calculate speed
        if len(window_points) < 2:
            continue
        
        # Calculate total distance and time across window
        first_point = window_points[0]
        last_point = window_points[-1]
        
        # Calculate cumulative distance
        total_distance = 0
        for j in range(len(window_points) - 1):
            p1 = window_points[j]
            p2 = window_points[j + 1]
            total_distance += haversine_distance(
                p1.latitude, p1.longitude,
                p2.latitude, p2.longitude
            )
        
        # Calculate time difference
        time_diff = (last_point.dateTime - first_point.dateTime).total_seconds()
        
        if time_diff > 0:
            speed = total_distance / time_diff  # m/s
            # Sanity check: max reasonable speed 50 m/s (180 km/h)
            if speed < 50.0:
                measurement.speed = speed
    
    # Second pass: Filter by acceleration to remove impossible speed changes
    # This catches GPS errors that passed the accuracy filter
    MAX_ACCELERATION = 10.0  # m/s² - maximum reasonable acceleration (includes vehicles)
    
    for i, measurement in enumerate(measurements):
        if measurement.speed is None:
            continue
        
        # Find previous measurement with valid speed
        prev_with_speed = None
        for j in range(i - 1, -1, -1):
            if measurements[j].speed is not None:
                prev_with_speed = measurements[j]
                break
        
        if prev_with_speed:
            # Calculate acceleration
            speed_diff = abs(measurement.speed - prev_with_speed.speed)  # m/s
            time_diff = (measurement.dateTime - prev_with_speed.dateTime).total_seconds()
            
            if time_diff > 0:
                acceleration = speed_diff / time_diff  # m/s²
                
                # If acceleration is too high, invalidate this speed
                if acceleration > MAX_ACCELERATION:
                    measurement.speed = None
    
    # Update speeds in database (bulk update)
    RadiationMeasurement.objects.bulk_update(measurements, ['speed'], batch_size=1000)
    
    # Calculate total distance traveled (sum of distances between consecutive points with good GPS)
    # Only use points with good accuracy to avoid inflating distance with GPS errors
    total_distance = 0.0
    MAX_ACCURACY_FOR_DISTANCE = 15.0  # Same threshold as speed calculation
    
    prev_point = None
    for measurement in measurements:
        # Only count distance for points with good GPS accuracy
        if measurement.accuracy is not None and measurement.accuracy < MAX_ACCURACY_FOR_DISTANCE:
            if prev_point is not None:
                # Calculate distance from previous good point
                distance = haversine_distance(
                    prev_point['lat'], prev_point['lon'],
                    measurement.latitude, measurement.longitude
                )
                # Sanity check: ignore unrealistic jumps > 500m between consecutive points
                if distance < 500.0:
                    total_distance += distance
            
            # Update previous point
            prev_point = {
                'lat': measurement.latitude,
                'lon': measurement.longitude
            }
    
    # Calculate average speed for the track (only valid speeds after filtering)
    speeds = [m.speed for m in measurements if m.speed is not None]
    average_speed = statistics.mean(speeds) if speeds else None
    
    # Calculate radiation statistics (dose rate)
    dose_rates = [m.dose_rate for m in measurements if m.dose_rate is not None]
    
    if dose_rates:
        min_dose_rate = min(dose_rates)
        max_dose_rate = max(dose_rates)
        avg_dose_rate = statistics.mean(dose_rates)
        std_dose_rate = statistics.stdev(dose_rates) if len(dose_rates) > 1 else 0.0
    else:
        min_dose_rate = None
        max_dose_rate = None
        avg_dose_rate = None
        std_dose_rate = None
    
    # Update track metadata: start/end times, average speed, total distance, and radiation stats
    track.start_time = min(timestamps)
    track.end_time = max(timestamps)
    track.average_speed = average_speed
    track.total_distance = total_distance
    track.min_dose_rate = min_dose_rate
    track.max_dose_rate = max_dose_rate
    track.avg_dose_rate = avg_dose_rate
    track.std_dose_rate = std_dose_rate
    track.save(update_fields=[
        'start_time', 'end_time', 'average_speed', 'total_distance',
        'min_dose_rate', 'max_dose_rate', 'avg_dose_rate', 'std_dose_rate'
    ])
    
    # Weather buckets are NOT assigned here. The periodic assign_pending_weather_buckets
    # sweeper buckets every measurement lacking a weather_cache (tracks / movement /
    # stations) — a single unified path — and fetch_pending_weather fills the buckets.

    return len(measurements)


def parse_json_track(track, file_content):
    """
    Parse JSON file (RadiaCode mobile app format) and create RadiationMeasurement objects in bulk.
    
    JSON Format:
        {
            "name": "track name",
            "description": "track description",
            "device": {"name": "RadiaCode-102#RC-102-007300", "id": "52:43:06:60:1C:84"},
            "startedAt": "2025-12-24T18:26:00.360054",
            "endedAt": "2025-12-24T18:57:56.225290",
            "requiredGpsAccuracyMeters": 10.0,
            "points": [
                {
                    "timestamp": "2025-12-24T18:26:05.360471",
                    "latitude": 41.74599,
                    "longitude": -1.0734704,
                    "altitude": 264.8056169088939,
                    "accuracyMeters": 2.7869999408721924,
                    "cpm": 335.390625,
                    "doseMicroSvPerHour": 0.07213783192128176
                },
                ...
            ]
        }
    
    Args:
        track (Track): Track instance that owns these measurements
        file_content (bytes | str): JSON file content to parse
        
    Returns:
        int: Number of measurements successfully created
    """
    import json
    from .models import RadiationMeasurement, LightPollutionMeasurement
    from math import radians, sin, cos, sqrt, atan2
    
    def haversine_distance(lat1, lon1, lat2, lon2):
        """Calculate distance between two GPS coordinates using Haversine formula. Returns distance in meters."""
        R = 6371000  # Earth radius in meters
        phi1, phi2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        
        a = sin(dphi/2)**2 + cos(phi1) * cos(phi2) * sin(dlambda/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return R * c
    
    # Decode if bytes
    if isinstance(file_content, bytes):
        file_content = file_content.decode('utf-8')
    
    # Parse JSON
    try:
        data = json.loads(file_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}")
    
    track_type = data.get('trackType') or 'radiation'
    track.track_type = track_type
    points = data.get('points', [])
    if not points:
        raise ValueError("No measurement points found in JSON")
    
    print(f"Parsing JSON track with {len(points)} points...")
    
    # Update Track metadata from JSON if present
    if isinstance(data.get('requiredGpsAccuracyMeters'), (int, float)):
        track.required_gps_accuracy_meters = float(data['requiredGpsAccuracyMeters'])
    if isinstance(data.get('synced'), bool):
        track.synced = data['synced']
    if data.get('syncedAt'):
        synced_at = parse_datetime(str(data['syncedAt']))
        if synced_at and timezone.is_naive(synced_at):
            synced_at = timezone.make_aware(synced_at)
        track.synced_at = synced_at
    if data.get('id'):
        track.cloud_track_id = str(data['id'])
    if data.get('description'):
        track.description = str(data['description'])

    # Phase 1: Create measurement objects (not saved yet)
    measurements = []
    timestamps = []
    
    for point in points:
        try:
            # Parse timestamp
            timestamp_str = point.get('timestamp')
            if not timestamp_str:
                continue
            
            dt = parse_datetime(timestamp_str)
            if dt and timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            elif not dt:
                continue
            
            timestamps.append(dt)
            
            # Extract shared fields
            latitude = float(point['latitude'])
            longitude = float(point['longitude'])
            altitude = point.get('altitude')
            accuracy = point.get('accuracyMeters')

            if track_type == 'light':
                measurement = LightPollutionMeasurement(
                    user=track.created_by,
                    device=track.device,
                    project=track.project,
                    campaign=track.campaign,
                    track=track,
                    latitude=latitude,
                    longitude=longitude,
                    location=Point(longitude, latitude),
                    altitude=altitude,
                    accuracy=accuracy,
                    speed=point.get('speed'),
                    lux=point.get('lux'),
                    cct=point.get('cct'),
                    cieX=point.get('cieX'),
                    cieY=point.get('cieY'),
                    cieU=point.get('cieU'),
                    cieV=point.get('cieV'),
                    duv=point.get('duv'),
                    tint=point.get('tint'),
                    mode=point.get('mode'),
                    channels=point.get('channels'),
                    temperature=point.get('temperature'),
                    batteryMv=point.get('batteryMv'),
                    dateTime=dt
                )
            else:
                cpm = point.get('cpm')
                dose_rate = point.get('doseMicroSvPerHour')
                # La app envía errores relativos (fracción); también aceptamos los
                # absolutos antiguos (cpmErr/doseMicroSvPerHourErr) por compatibilidad.
                cpm_rel_error = point.get('cpmRelErr')
                dose_rate_rel_error = point.get('doseMicroSvPerHourRelErr')
                cpm_error = point.get('cpmErr')
                if cpm_error is None and cpm is not None and cpm_rel_error is not None:
                    cpm_error = cpm * cpm_rel_error
                dose_rate_error = point.get('doseMicroSvPerHourErr')
                if dose_rate_error is None and dose_rate is not None and dose_rate_rel_error is not None:
                    dose_rate_error = dose_rate * dose_rate_rel_error
                measurement = RadiationMeasurement(
                    user=track.created_by,
                    device=track.device,
                    project=track.project,
                    campaign=track.campaign,
                    track=track,
                    latitude=latitude,
                    longitude=longitude,
                    location=Point(longitude, latitude),
                    altitude=altitude,
                    accuracy=accuracy,
                    dose_rate=dose_rate,
                    dose_rate_error=dose_rate_error,
                    dose_rate_rel_error=dose_rate_rel_error,
                    radiation_unit="μSv/h",
                    cpm=int(cpm) if cpm is not None else None,
                    cpm_error=cpm_error,
                    cpm_rel_error=cpm_rel_error,
                    speed=None,  # Will be calculated later
                    dateTime=dt
                )
            measurements.append(measurement)
            
        except (ValueError, KeyError, TypeError) as e:
            print(f"  Warning: Skipping invalid point: {e}")
            continue
    
    if not measurements:
        raise ValueError("No valid measurements could be parsed from JSON")
    
    print(f"  Parsed {len(measurements)} valid measurements")
    
    # Phase 2: Bulk create measurements
    print(f"Phase 2: Bulk creating measurements...")
    if track_type == 'light':
        LightPollutionMeasurement.objects.bulk_create(measurements, batch_size=1000)
    else:
        RadiationMeasurement.objects.bulk_create(measurements, batch_size=1000)
    print(f"  Created {len(measurements)} measurements")
    
    # Phase 3: per-point speed from GPS (shared helper) for radiation tracks
    if track_type != 'light':
        print(f"Phase 3: Calculating speeds...")
        compute_point_speeds(measurements)
        RadiationMeasurement.objects.bulk_update(measurements, ['speed'], batch_size=1000)
    
    # Phase 4: Calculate track statistics
    print(f"Phase 4: Calculating track statistics...")
    
    # Total distance
    total_distance = 0.0
    MAX_ACCURACY_FOR_DISTANCE = 15.0
    prev_point = None
    
    for measurement in measurements:
        if measurement.accuracy is not None and measurement.accuracy < MAX_ACCURACY_FOR_DISTANCE:
            if prev_point is not None:
                distance = haversine_distance(
                    prev_point['lat'], prev_point['lon'],
                    measurement.latitude, measurement.longitude
                )
                if distance < 500.0:
                    total_distance += distance
            prev_point = {'lat': measurement.latitude, 'lon': measurement.longitude}
    
    # Average speed
    speeds = [m.speed for m in measurements if m.speed is not None]
    average_speed = statistics.mean(speeds) if speeds else None
    
    # Radiation statistics (only for radiation tracks)
    if track_type != 'light':
        dose_rates = [m.dose_rate for m in measurements if m.dose_rate is not None]
        if dose_rates:
            min_dose_rate = min(dose_rates)
            max_dose_rate = max(dose_rates)
            avg_dose_rate = statistics.mean(dose_rates)
            std_dose_rate = statistics.stdev(dose_rates) if len(dose_rates) > 1 else 0.0
        else:
            min_dose_rate = None
            max_dose_rate = None
            avg_dose_rate = None
            std_dose_rate = None
    
    # Update track metadata
    track.start_time = min(timestamps)
    track.end_time = max(timestamps)
    track.average_speed = average_speed
    track.total_distance = total_distance

    base_update_fields = [
        'track_type',
        'description',
        'required_gps_accuracy_meters',
        'synced',
        'synced_at',
        'cloud_track_id',
        'start_time',
        'end_time',
        'average_speed',
        'total_distance',
    ]

    if track_type != 'light':
        track.min_dose_rate = min_dose_rate
        track.max_dose_rate = max_dose_rate
        track.avg_dose_rate = avg_dose_rate
        track.std_dose_rate = std_dose_rate
        base_update_fields.extend(['min_dose_rate', 'max_dose_rate', 'avg_dose_rate', 'std_dose_rate'])

    track.save(update_fields=base_update_fields)

    # Weather buckets are NOT assigned here. The periodic assign_pending_weather_buckets
    # sweeper buckets every measurement lacking a weather_cache (tracks / movement /
    # stations) — a single unified path — and fetch_pending_weather fills the buckets.

    # Phase 6: Create gamma spectra (RadiaCode JSON only)
    # Spectra arrive as a root-level `spectra` array (sibling of `points`).
    # Optional and backward-compatible: tracks without spectra are unaffected.
    # An invalid spectrum is skipped with a warning; it never fails the track.
    spectra_data = data.get('spectra') or []
    if spectra_data:
        from .models import Spectrum
        print(f"Phase 6: Creating spectra from {len(spectra_data)} entries...")
        spectra = []
        for spec in spectra_data:
            try:
                channel_count = int(spec['channelCount'])
                counts = spec['counts']
                if not isinstance(counts, list) or len(counts) != channel_count:
                    got = len(counts) if isinstance(counts, list) else type(counts).__name__
                    print(f"  Warning: Skipping spectrum '{spec.get('name')}': "
                          f"counts length {got} != channelCount {channel_count}")
                    continue

                started_at = parse_datetime(str(spec['startedAt']))
                ended_at = parse_datetime(str(spec['endedAt']))
                if started_at and timezone.is_naive(started_at):
                    started_at = timezone.make_aware(started_at)
                if ended_at and timezone.is_naive(ended_at):
                    ended_at = timezone.make_aware(ended_at)
                if not started_at or not ended_at:
                    print(f"  Warning: Skipping spectrum '{spec.get('name')}': invalid startedAt/endedAt")
                    continue

                spectra.append(Spectrum(
                    track=track,
                    name=str(spec['name']),
                    index=int(spec['index']),
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_sec=int(spec['durationSec']),
                    a0=float(spec['a0']),
                    a1=float(spec['a1']),
                    a2=float(spec['a2']),
                    channel_count=channel_count,
                    counts=counts,
                    start_lat=float(spec['startLat']) if spec.get('startLat') is not None else None,
                    start_lon=float(spec['startLon']) if spec.get('startLon') is not None else None,
                    start_alt=float(spec['startAlt']) if spec.get('startAlt') is not None else None,
                    end_lat=float(spec['endLat']) if spec.get('endLat') is not None else None,
                    end_lon=float(spec['endLon']) if spec.get('endLon') is not None else None,
                ))
            except (ValueError, KeyError, TypeError) as e:
                print(f"  Warning: Skipping invalid spectrum: {e}")
                continue

        if spectra:
            Spectrum.objects.bulk_create(spectra, batch_size=500)
        print(f"  Created {len(spectra)} spectra ({len(spectra_data) - len(spectra)} skipped)")

    return len(measurements)


def parse_gpx_track(track, file_content):
    """
    Parse GPX file and create measurements
    TODO: Implement GPX parsing
    """
    raise NotImplementedError("GPX parsing not yet implemented")


def assign_pending_weather_buckets(limit=5000):
    """
    Assign a (H3 cell res 8 + hour) WeatherCache bucket to every measurement that
    lacks one — across ALL sources: tracks, movement single-uploads and station
    readings. This is the SINGLE, unified bucketing path.

    Ingest/upload code no longer buckets anything: it just saves measurements.
    This scheduled task groups whatever is pending by (H3 cell, hour), creates or
    reuses the bucket, and links the measurements in bulk. The actual weather API
    call is then performed by ``fetch_pending_weather`` over pending buckets.

    Run it on a short schedule (e.g. every few minutes) ahead of
    ``fetch_pending_weather``. Idempotent and self-healing: anything left
    unbucketed (e.g. a partial failure) is picked up on the next pass.

    Same H3 resolution (8) and hour-rounding as the historical inline logic, so a
    station reporting every 5 min within one cell/hour shares ONE bucket and thus
    triggers at most one weather fetch per hour.

    Args:
        limit (int): max measurements to process per model per run.

    Returns:
        dict: per-model counts of measurements bucketed and buckets touched.
    """
    from .models import RadiationMeasurement, LightPollutionMeasurement, WeatherCache

    results = {}
    for model in (RadiationMeasurement, LightPollutionMeasurement):
        pending = list(
            model.objects
            .filter(weather_cache__isnull=True)
            .only('id', 'latitude', 'longitude', 'dateTime')
            .order_by('id')[:limit]
        )

        # Group pending measurements by (H3 cell, hour) in UTC.
        groups = {}
        for m in pending:
            if m.latitude is None or m.longitude is None or m.dateTime is None:
                continue
            try:
                h3_cell = h3.latlng_to_cell(float(m.latitude), float(m.longitude), 8)
            except Exception as exc:
                logger.warning(f"assign_pending_weather_buckets: H3 falló para medida {m.id}: {exc}")
                continue
            dt_utc = m.dateTime
            if dt_utc.tzinfo is None:
                dt_utc = timezone.make_aware(dt_utc, timezone.utc)
            else:
                dt_utc = dt_utc.astimezone(timezone.utc)
            hour = dt_utc.replace(minute=0, second=0, microsecond=0)
            groups.setdefault((h3_cell, hour), []).append(m)

        # Create/reuse buckets and link measurements.
        to_update = []
        bucket_ids = set()
        for (h3_cell, hour), members in groups.items():
            rep = members[0]
            cache, _ = WeatherCache.objects.get_or_create(
                h3_cell=h3_cell,
                timestamp_hour=hour,
                defaults={
                    'latitude': rep.latitude,
                    'longitude': rep.longitude,
                    'fetched': False,
                    'fetch_attempts': 0,
                },
            )
            bucket_ids.add(cache.id)
            for m in members:
                m.weather_cache = cache
                to_update.append(m)

        if to_update:
            model.objects.bulk_update(to_update, ['weather_cache'], batch_size=1000)

        results[model.__name__] = {
            'measurements': len(to_update),
            'buckets': len(bucket_ids),
        }

    logger.info(f"assign_pending_weather_buckets: {results}")
    return results


def fetch_pending_weather(limit=100, max_attempts=3):
    """
    Fetch weather data for all pending WeatherCache entries globally.
    
    This is a scheduler task (e.g., run every hour) that processes WeatherCache
    entries that haven't been fetched yet or failed previously. Processes entries
    from all tracks, not just one specific track.
    
    Args:
        limit (int): Maximum number of WeatherCache entries to process per run
        max_attempts (int): Skip entries that have failed this many times
        
    Returns:
        dict: Result with keys:
            - success (bool): True if fetch succeeded
            - caches_processed (int): Number of WeatherCache entries processed
            - caches_updated (int): Number successfully updated with weather data
            - caches_skipped (int): Number skipped (too many attempts)
            - errors (list): List of error messages if any
    
    Usage:
        As RQ scheduled task (e.g., every hour):
        >>> from django_rq import get_scheduler
        >>> scheduler = get_scheduler('openred-weather')
        >>> scheduler.schedule(
        ...     scheduled_time=datetime.utcnow(),
        ...     func=fetch_pending_weather,
        ...     args=[],
        ...     kwargs={'limit': 100},
        ...     interval=3600,  # Every hour
        ...     repeat=None  # Repeat indefinitely
        ... )
        
        Or manual execution:
        >>> from django_rq import enqueue
        >>> enqueue(fetch_pending_weather, limit=50)
    
    Example:
        Run this periodically to fetch weather for all pending entries:
        $ python manage.py shell
        >>> from measures.tasks import fetch_pending_weather
        >>> result = fetch_pending_weather(limit=100)
        >>> print(f"Updated {result['caches_updated']} entries")
    """
    from .models import WeatherCache
    
    print(f"Fetching pending weather data (limit={limit}, max_attempts={max_attempts})...")
    
    try:
        # Get pending WeatherCache entries (not fetched yet, attempts < max)
        pending_caches = WeatherCache.objects.filter(
            fetched=False,
            fetch_attempts__lt=max_attempts
        ).order_by('timestamp_hour')[:limit]
        
        total_pending = pending_caches.count()
        
        if total_pending == 0:
            print("  No pending weather caches found")
            return {
                'success': True,
                'caches_processed': 0,
                'caches_updated': 0,
                'caches_skipped': 0,
                'message': 'No pending weather caches'
            }
        
        print(f"  Found {total_pending} pending weather cache entries")
        
        # Batch fetch weather data
        result = fetch_weather_batch(list(pending_caches))
        
        # Count skipped entries (too many attempts)
        skipped = WeatherCache.objects.filter(
            fetched=False,
            fetch_attempts__gte=max_attempts
        ).count()
        
        print(f"  Weather fetch completed: {result['updated']}/{result['total']} entries updated")
        if skipped > 0:
            print(f"  Note: {skipped} entries skipped (max attempts reached)")
        
        return {
            'success': True,
            'caches_processed': result['total'],
            'caches_updated': result['updated'],
            'caches_skipped': skipped,
            'errors': result.get('errors', [])
        }
        
    except Exception as e:
        print(f"  Error in fetch_pending_weather: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def fetch_weather_for_track(track_id):
    """
    Fetch weather data for all pending WeatherCache entries in a specific track.
    
    DEPRECATED: Use fetch_pending_weather() instead for scheduler-based processing.
    
    This function is kept for backward compatibility or manual track-specific processing.
    
    Args:
        track_id (int): ID of the track to fetch weather for
        
    Returns:
        dict: Result with processing statistics
    """
    from .models import Track, WeatherCache, RadiationMeasurement
    
    try:
        track = Track.objects.get(id=track_id)
        print(f"Fetching weather data for track {track.id} ({track.name})...")
        
        # Get unique WeatherCache IDs for this track that haven't been fetched
        cache_ids = RadiationMeasurement.objects.filter(
            track=track,
            weather_cache__isnull=False,
            weather_cache__fetched=False
        ).values_list('weather_cache_id', flat=True).distinct()
        
        if not cache_ids:
            print(f"  No pending weather caches for track {track.id}")
            return {
                'success': True,
                'track_id': track_id,
                'caches_processed': 0,
                'caches_updated': 0,
                'message': 'No pending weather caches'
            }
        
        caches = WeatherCache.objects.filter(id__in=cache_ids, fetched=False)
        print(f"  Found {len(caches)} weather cache entries to fetch")
        
        # Batch fetch weather data
        result = fetch_weather_batch(list(caches))
        
        print(f"  Weather fetch completed: {result['updated']}/{result['total']} entries updated")
        
        return {
            'success': True,
            'track_id': track_id,
            'caches_processed': result['total'],
            'caches_updated': result['updated'],
            'errors': result.get('errors', [])
        }
        
    except Track.DoesNotExist:
        return {
            'success': False,
            'track_id': track_id,
            'error': f'Track {track_id} not found'
        }
    except Exception as e:
        print(f"  Error fetching weather for track {track_id}: {e}")
        return {
            'success': False,
            'track_id': track_id,
            'error': str(e)
        }


def fetch_weather_batch(caches):
    """
    Fetch weather data for multiple WeatherCache entries from Open-Meteo API.
    
    Groups caches by H3 cell (location) and fetches weather for date ranges
    in batch to minimize API calls. A single API call per location can fetch
    data for multiple hours/days.
    
    Args:
        caches (list): List of WeatherCache objects to fetch weather for
        
    Returns:
        dict: Result with keys:
            - total (int): Total caches processed
            - updated (int): Number successfully updated
            - errors (list): List of error messages
    
    Open-Meteo API:
        - Endpoint: https://archive-api.open-meteo.com/v1/archive
        - Free, no API key required
        - Historical data from 1940 to 5 days ago
        - Hourly data available
    
    Example:
        >>> caches = WeatherCache.objects.filter(fetched=False)[:10]
        >>> result = fetch_weather_batch(list(caches))
        >>> print(f"Updated {result['updated']} of {result['total']}")
    """
    print(f"  Fetching weather for {len(caches)} cache entries...")
    
    # Group caches by H3 cell (same location)
    by_location = defaultdict(list)
    for cache in caches:
        by_location[cache.h3_cell].append(cache)
    
    print(f"  Grouped into {len(by_location)} unique locations")
    
    updated_count = 0
    errors = []
    
    for h3_cell, cell_caches in by_location.items():
        try:
            # Get date range for this location group
            min_date = min(c.timestamp_hour for c in cell_caches).date()
            max_date = max(c.timestamp_hour for c in cell_caches).date()
            
            # Use first cache as representative for lat/lon
            lat = float(cell_caches[0].latitude)
            lon = float(cell_caches[0].longitude)
            
            print(f"  Fetching {h3_cell}: {len(cell_caches)} hours from {min_date} to {max_date}")
            
            # Single API call for entire date range at this location
            url = "https://archive-api.open-meteo.com/v1/archive"
            params = {
                'latitude': lat,
                'longitude': lon,
                'start_date': min_date.isoformat(),
                'end_date': max_date.isoformat(),
                'hourly': 'temperature_2m,relative_humidity_2m,pressure_msl,wind_speed_10m,wind_direction_10m,cloud_cover',
                'daily': 'rain_sum',
                'timezone': 'UTC'
            }
            
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if 'hourly' not in data:
                errors.append(f"No hourly data in response for {h3_cell}")
                continue
            
            hourly = data['hourly']
            daily = data.get('daily', {})
            
            # Create a mapping of date -> daily rain sum for fast lookup
            rain_by_date = {}
            if 'time' in daily and 'rain_sum' in daily:
                for i, date_str in enumerate(daily['time']):
                    date_obj = datetime.fromisoformat(date_str).date()
                    rain_by_date[date_obj] = daily['rain_sum'][i]
            
            # Create a mapping of hour -> weather data for fast lookup
            weather_by_hour = {}
            for i, time_str in enumerate(hourly['time']):
                hour_dt = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
                weather_by_hour[hour_dt] = {
                    'temperature': hourly['temperature_2m'][i],
                    'humidity': hourly['relative_humidity_2m'][i],
                    'pressure': hourly['pressure_msl'][i],
                    'wind_speed': hourly['wind_speed_10m'][i],
                    'wind_direction': hourly['wind_direction_10m'][i],
                    'cloud_cover': hourly['cloud_cover'][i]
                }
            
            # Update each WeatherCache with its hour's data
            for cache in cell_caches:
                # Ensure cache timestamp is timezone-aware
                cache_time = cache.timestamp_hour
                if cache_time.tzinfo is None:
                    cache_time = timezone.make_aware(cache_time, timezone.utc)
                
                if cache_time in weather_by_hour:
                    weather = weather_by_hour[cache_time]
                    cache.temperature = weather['temperature']
                    cache.humidity = weather['humidity']
                    cache.pressure = weather['pressure']
                    cache.wind_speed = weather['wind_speed']
                    cache.wind_direction = weather['wind_direction']
                    cache.cloud_cover = weather['cloud_cover']
                    # Add daily rain sum for this date
                    cache_date = cache_time.date()
                    cache.rain_sum = rain_by_date.get(cache_date)
                    cache.weather_data = data  # Store complete response
                    cache.fetched = True
                    cache.fetch_attempts += 1
                    cache.last_fetch_attempt = timezone.now()
                    cache.save()
                    updated_count += 1
                else:
                    error_msg = f"No weather data for {cache.h3_cell} @ {cache_time}"
                    errors.append(error_msg)
                    print(f"    Warning: {error_msg}")
                    # Still update fetch metadata
                    cache.fetch_attempts += 1
                    cache.last_fetch_attempt = timezone.now()
                    cache.save(update_fields=['fetch_attempts', 'last_fetch_attempt'])
            
        except requests.RequestException as e:
            error_msg = f"API error for {h3_cell}: {str(e)}"
            errors.append(error_msg)
            print(f"    Error: {error_msg}")
            # Update fetch attempts for failed caches
            for cache in cell_caches:
                cache.fetch_attempts += 1
                cache.last_fetch_attempt = timezone.now()
                cache.save(update_fields=['fetch_attempts', 'last_fetch_attempt'])
        except Exception as e:
            error_msg = f"Unexpected error for {h3_cell}: {str(e)}"
            errors.append(error_msg)
            print(f"    Error: {error_msg}")
    
    print(f"  Batch fetch complete: {updated_count}/{len(caches)} entries updated")

    return {
        'total': len(caches),
        'updated': updated_count,
        'errors': errors
    }


# ---------------------------------------------------------------------------
# Phase 4: movement sessionization + station watchdog
# ---------------------------------------------------------------------------

def _haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres between two lat/lon points."""
    r = 6371000.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlmb = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def compute_point_speeds(points, window_size=5, max_accuracy=15.0,
                         max_speed=50.0, max_acceleration=10.0):
    """
    Per-point speed (m/s) from GPS positions — single shared implementation used by
    the track parsers and the movement grouping job.

    Smooths over GPS jitter with a moving window (only points with accuracy <
    max_accuracy count), discards readings above max_speed, then drops any speed
    whose implied acceleration vs the previous valid point exceeds max_acceleration.
    Points MUST be ordered by dateTime. Mutates each point's ``speed`` in place
    (leaving None where it can't be computed); the caller persists via bulk_update.
    """
    n = len(points)
    for i in range(n):
        start = max(0, i - window_size // 2)
        end = min(n, i + window_size // 2 + 1)
        window = [m for m in points[start:end]
                  if m.accuracy is not None and m.accuracy < max_accuracy]
        if len(window) < 2:
            continue
        distance = 0.0
        for a, b in zip(window, window[1:]):
            distance += _haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
        dt = (window[-1].dateTime - window[0].dateTime).total_seconds()
        if dt > 0:
            speed = distance / dt
            if speed < max_speed:
                points[i].speed = speed

    # Acceleration filter: invalidate physically impossible jumps.
    for i in range(n):
        if points[i].speed is None:
            continue
        prev = None
        for j in range(i - 1, -1, -1):
            if points[j].speed is not None:
                prev = points[j]
                break
        if prev is not None:
            dv = abs(points[i].speed - prev.speed)
            dt = (points[i].dateTime - prev.dateTime).total_seconds()
            if dt > 0 and dv / dt > max_acceleration:
                points[i].speed = None
    return points


def recompute_track_stats(track):
    """
    Recompute and persist a radiation track's aggregate stats from its measurements.

    Single source of truth for the stats shown on a Track: time span, count, dose
    min/max/avg/std, total distance (haversine over ordered points) and average
    speed. Used by the movement grouping job; safe to call on any radiation track.
    """
    from .models import RadiationMeasurement

    points = list(
        RadiationMeasurement.objects
        .filter(track=track)
        .order_by('dateTime')
        .only('dateTime', 'latitude', 'longitude', 'dose_rate', 'speed')
    )
    if not points:
        track.total_measurements = 0
        track.save(update_fields=['total_measurements'])
        return track

    track.start_time = points[0].dateTime
    track.end_time = points[-1].dateTime
    track.total_measurements = len(points)

    doses = [p.dose_rate for p in points if p.dose_rate is not None]
    if doses:
        track.min_dose_rate = min(doses)
        track.max_dose_rate = max(doses)
        track.avg_dose_rate = sum(doses) / len(doses)
        track.std_dose_rate = statistics.pstdev(doses) if len(doses) > 1 else 0.0

    total_distance = 0.0
    for a, b in zip(points, points[1:]):
        if None in (a.latitude, a.longitude, b.latitude, b.longitude):
            continue
        total_distance += _haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
    track.total_distance = total_distance

    duration = (track.end_time - track.start_time).total_seconds() if track.end_time and track.start_time else 0
    speeds = [p.speed for p in points if p.speed is not None]
    if speeds:
        track.average_speed = sum(speeds) / len(speeds)
    elif duration > 0:
        track.average_speed = total_distance / duration

    track.save(update_fields=[
        'start_time', 'end_time', 'total_measurements',
        'min_dose_rate', 'max_dose_rate', 'avg_dose_rate', 'std_dose_rate',
        'total_distance', 'average_speed',
    ])
    return track


def group_movement_measurements(gap_seconds=900, min_points=2, limit=20000):
    """
    Sessionize loose ``movement`` radiation measurements into Tracks.

    Loose points (capture_mode='movement', track IS NULL) are grouped per
    (device, project) and split into sessions wherever the time gap between
    consecutive points exceeds ``gap_seconds``. Each closed session becomes a
    Track; its measurements are linked and stats recomputed via
    recompute_track_stats.

    The most recent session of a (device, project) is left OPEN — not turned into
    a track — while its newest reading arrived within ``gap_seconds`` (by server
    received_at, so a wrong device clock can't prematurely close it). Those points
    stay loose and visible on the map until the device goes quiet, then the next
    run closes them. Idempotent: only touches points with track IS NULL.

    Args:
        gap_seconds (int): max gap within a session / quiet window to close it.
        min_points (int): sessions with fewer points are skipped (left loose).
        limit (int): max loose points scanned per run.

    Returns:
        dict: number of tracks created and measurements grouped.
    """
    from .models import RadiationMeasurement, Track

    now = timezone.now()
    pending = RadiationMeasurement.objects.filter(capture_mode='movement', track__isnull=True)
    keys = list(pending.values_list('device_id', 'project_id').distinct()[:limit])

    tracks_created = 0
    grouped = 0
    for device_id, project_id in keys:
        points = list(
            pending.filter(device_id=device_id, project_id=project_id)
            .order_by('dateTime')
            .only('id', 'dateTime', 'received_at', 'user')
        )
        if not points:
            continue

        # Split into sessions by temporal gap.
        sessions = []
        current = [points[0]]
        for prev, point in zip(points, points[1:]):
            if (point.dateTime - prev.dateTime).total_seconds() > gap_seconds:
                sessions.append(current)
                current = []
            current.append(point)
        sessions.append(current)

        for idx, session in enumerate(sessions):
            is_last = (idx == len(sessions) - 1)
            if is_last:
                newest = max((p.received_at or p.dateTime) for p in session)
                if (now - newest).total_seconds() <= gap_seconds:
                    continue  # still open; leave for a later run
            if len(session) < min_points:
                continue

            track = Track.objects.create(
                project_id=project_id,
                device_id=device_id,
                track_type='radiation',
                file_type='json',
                status='completed',
                description='Auto-agrupado desde medidas movement',
                created_by=session[0].user,
            )
            ids = [p.id for p in session]
            RadiationMeasurement.objects.filter(id__in=ids).update(track=track)

            # Per-point speed from GPS (same logic as track uploads), then stats.
            pts = list(
                RadiationMeasurement.objects.filter(track=track)
                .order_by('dateTime')
                .only('id', 'dateTime', 'latitude', 'longitude', 'accuracy', 'speed')
            )
            compute_point_speeds(pts)
            RadiationMeasurement.objects.bulk_update(pts, ['speed'], batch_size=1000)

            recompute_track_stats(track)
            tracks_created += 1
            grouped += len(ids)

    result = {'tracks_created': tracks_created, 'measurements_grouped': grouped}
    logger.info(f"group_movement_measurements: {result}")
    return result


def check_station_health(grace_factor=2):
    """
    Station watchdog: flag stations as online/offline by reporting cadence.

    For each active station, a reading is "overdue" when
    ``now - last_measurement_at > expected_interval_seconds * grace_factor`` (using
    the server-side received_at stored in last_measurement_at, not the device
    clock). Overdue -> 'offline'; recent -> 'online'; never-reported -> 'unknown'.
    Status transitions are logged so a notification channel can hook in later.

    Returns:
        dict: counts per resulting status and the list of stations that changed.
    """
    from .models import Station

    now = timezone.now()
    counts = {'online': 0, 'offline': 0, 'unknown': 0}
    transitions = []

    for station in Station.objects.filter(is_active=True):
        previous = station.status
        if station.last_measurement_at is None:
            new_status = 'unknown'
        else:
            overdue = (now - station.last_measurement_at).total_seconds()
            threshold = station.expected_interval_seconds * grace_factor
            new_status = 'offline' if overdue > threshold else 'online'

        counts[new_status] += 1
        if new_status != previous:
            station.status = new_status
            station.save(update_fields=['status', 'updated_at'])
            transitions.append({'station_id': station.id, 'name': station.name,
                                'from': previous, 'to': new_status})
            # TODO(notify): wire an alert channel here when a station goes offline.
            logger.warning(
                f"check_station_health: estación {station.id} ({station.name}) "
                f"{previous} -> {new_status}"
            )

    result = {'counts': counts, 'transitions': transitions}
    logger.info(f"check_station_health: {counts}")
    return result


# =============================================================================
# Contribution milestones (300k / 400k / ... measurements -> thank-you email)
# =============================================================================

def count_project_contributions(project_name=None):
    """
    Number of contributions (radiation + light pollution measurements) filed
    under the milestone project (settings.CONTRIBUTION_MILESTONE_PROJECT).

    Returns 0 if the project does not exist.
    """
    from django.conf import settings
    from missions.models import Project
    from .models import RadiationMeasurement, LightPollutionMeasurement

    name = project_name or getattr(settings, 'CONTRIBUTION_MILESTONE_PROJECT', 'Openred')
    project = Project.objects.filter(name=name).first()
    if project is None:
        logger.warning(f"Milestone project '{name}' not found; counting 0 contributions")
        return 0
    return (
        RadiationMeasurement.objects.filter(project=project).count()
        + LightPollutionMeasurement.objects.filter(project=project).count()
    )


def milestone_thresholds_reached(total):
    """All configured thresholds <= total, ascending (e.g. [300000, 400000])."""
    from django.conf import settings
    start = getattr(settings, 'CONTRIBUTION_MILESTONE_START', 300000)
    step = getattr(settings, 'CONTRIBUTION_MILESTONE_STEP', 100000)
    if total < start or step <= 0:
        return []
    return list(range(start, total + 1, step))


def check_contribution_milestones():
    """
    Detect newly reached contribution milestones and enqueue the thank-you email.

    Runs periodically (RQ_JOBS, daily). Safe to run concurrently: the unique ``threshold`` column guarantees a milestone is
    created (and therefore emailed) once.

    Bootstrap: the first time it runs with an empty table, every threshold
    already passed is recorded as ``backfilled`` and NOT emailed, so enabling
    the feature never mass-mails users for milestones that are old news. Use
    ``manage.py contribution_milestone send --threshold N`` to send one of
    those by hand.

    Returns:
        dict: {'total': int, 'new': [thresholds enqueued], 'backfilled': [...]}
    """
    from django.db import IntegrityError
    from .models import ContributionMilestone

    total = count_project_contributions()
    reached = milestone_thresholds_reached(total)
    result = {'total': total, 'new': [], 'backfilled': []}
    if not reached:
        return result

    bootstrap = not ContributionMilestone.objects.exists()
    for threshold in reached:
        if ContributionMilestone.objects.filter(threshold=threshold).exists():
            continue
        status = 'backfilled' if bootstrap else 'pending'
        try:
            milestone = ContributionMilestone.objects.create(
                threshold=threshold, total_at_detection=total, status=status,
            )
        except IntegrityError:
            # Another worker got there first — that one sends the email.
            continue
        if status == 'backfilled':
            result['backfilled'].append(threshold)
            continue
        result['new'].append(threshold)
        try:
            import django_rq
            django_rq.get_queue('openred-tracks').enqueue(
                'measures.tasks.send_contribution_milestone_email',
                milestone.id,
                job_timeout='30m',
                result_ttl=86400,
                job_id=f'milestone_email_{threshold}',
            )
        except Exception as enqueue_error:
            logger.error(f"Failed to enqueue milestone email {threshold}: {enqueue_error}")
            milestone.status = 'failed'
            milestone.error_message = f'enqueue: {enqueue_error}'
            milestone.save(update_fields=['status', 'error_message'])

    if result['new'] or result['backfilled']:
        logger.info(f"Contribution milestones: total={total} new={result['new']} backfilled={result['backfilled']}")
    return result


def milestone_recipients(project_name=None):
    """
    Distinct emails (case-insensitive) of active users who have contributed at
    least one measurement to the milestone project. These are the people the
    thank-you is addressed to; users without contributions are not mailed.
    """
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from missions.models import Project
    from .models import RadiationMeasurement, LightPollutionMeasurement

    name = project_name or getattr(settings, 'CONTRIBUTION_MILESTONE_PROJECT', 'Openred')
    project = Project.objects.filter(name=name).first()
    if project is None:
        return []
    user_ids = set(
        RadiationMeasurement.objects.filter(project=project, user__isnull=False)
        .values_list('user_id', flat=True).distinct()
    ) | set(
        LightPollutionMeasurement.objects.filter(project=project, user__isnull=False)
        .values_list('user_id', flat=True).distinct()
    )
    User = get_user_model()
    seen = {}
    for email in (User.objects.filter(id__in=user_ids, is_active=True)
                  .exclude(email='').values_list('email', flat=True)):
        key = email.strip().lower()
        if key and key not in seen:
            seen[key] = email.strip()
    return sorted(seen.values(), key=str.lower)


def build_milestone_email(threshold, to_email, connection=None):
    """
    Render the bilingual thank-you email for ``threshold`` addressed to one
    recipient (one message per user so addresses are never exposed).
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    context = {
        'threshold': threshold,
        'threshold_es': f'{threshold:,}'.replace(',', '.'),
        'threshold_en': f'{threshold:,}',
        'site_url': getattr(settings, 'FRONTEND_URL', 'https://map.open-red.es'),
    }
    subject = render_to_string('emails/contribution_milestone_subject.txt', context).strip()
    text_body = render_to_string('emails/contribution_milestone.txt', context)
    html_body = render_to_string('emails/contribution_milestone.html', context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=[to_email],
        connection=connection,
    )
    message.attach_alternative(html_body, 'text/html')
    return message


def send_contribution_milestone_email(milestone_id, recipients=None):
    """
    Send the milestone thank-you to every active user (or to ``recipients``).

    Marks the ContributionMilestone as sent/failed. Refuses to re-send a
    milestone that is already ``sent`` or ``sending``.

    Returns:
        dict: {'milestone_id', 'threshold', 'sent': int, 'failed': int}
    """
    from django.core.mail import get_connection
    from .models import ContributionMilestone

    milestone = ContributionMilestone.objects.get(id=milestone_id)
    if milestone.status in ('sent', 'sending'):
        logger.warning(f"Milestone {milestone.threshold} already {milestone.status}; skipping")
        return {'milestone_id': milestone_id, 'threshold': milestone.threshold, 'sent': 0, 'failed': 0,
                'skipped': milestone.status}

    milestone.status = 'sending'
    milestone.save(update_fields=['status'])

    recipients = list(recipients) if recipients is not None else milestone_recipients()
    sent = failed = 0
    errors = []
    try:
        connection = get_connection()
        connection.open()
        for email in recipients:
            try:
                build_milestone_email(milestone.threshold, email, connection=connection).send()
                sent += 1
            except Exception as send_error:
                failed += 1
                errors.append(f'{email}: {send_error}')
                logger.error(f"Milestone {milestone.threshold} email to {email} failed: {send_error}")
        connection.close()
    except Exception as conn_error:
        errors.append(f'connection: {conn_error}')
        logger.error(f"Milestone {milestone.threshold} mail connection failed: {conn_error}")

    milestone.recipients_count = sent
    milestone.failed_count = failed
    milestone.error_message = '\n'.join(errors)[:10000]
    milestone.sent_at = timezone.now()
    milestone.status = 'sent' if sent > 0 else 'failed'
    milestone.save(update_fields=['recipients_count', 'failed_count', 'error_message', 'sent_at', 'status'])

    logger.info(f"Milestone {milestone.threshold}: sent={sent} failed={failed}")
    return {'milestone_id': milestone_id, 'threshold': milestone.threshold, 'sent': sent, 'failed': failed}
