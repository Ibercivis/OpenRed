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
import math
import statistics
import requests
import h3
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import datetime, timedelta
from collections import defaultdict


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
        
        # ✅ Enqueue weather fetching task for this track's measurements
        try:
            import django_rq
            queue = django_rq.get_queue('openred-weather')
            weather_job = queue.enqueue(
                'measures.tasks.fetch_pending_weather',
                limit=measurements_count + 50,  # Fetch slightly more than measurements count
                max_attempts=3,
                job_timeout='30m',  # 30 minutes timeout for large tracks
                result_ttl=3600,  # Keep result for 1 hour
                job_id=f'weather_track_{track_id}_{timezone.now().timestamp()}'
            )
            print(f"✅ Weather task enqueued: {weather_job.id} for track {track_id}")
        except Exception as weather_error:
            print(f"⚠️ Failed to enqueue weather task for track {track_id}: {weather_error}")
            # Don't fail the whole process if weather queueing fails
        
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
        
        return {
            'success': False,
            'error': str(e),
            'track_id': track_id
        }


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
        measurement = RadiationMeasurement(
            user=track.created_by,
            device=track.device,
            project=track.project,
            track=track,
            latitude=float(row.get('latitude', 0)),
            longitude=float(row.get('longitude', 0)),
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


def parse_rctrk_track(track, file_content):
    """
    Parse RCTRK file (RadiaCode format) and create RadiationMeasurement objects in bulk.
    
    RCTRK Format:
        Line 1: Track: <name>\t<device>\t \tEC
        Line 2: Headers (tab-separated): Timestamp\tTime\tLatitude\tLongitude\tAccuracy\tDoseRate\tCountRate\tComment
        Line 3+: Data rows (tab-separated)
    
    Example:
        Track: 2025-08-27vistabella-ruta	RC-102-008858	 	EC
        Timestamp	Time	Latitude	Longitude	Accuracy	DoseRate	CountRate	Comment
        134007603713620000	2025-08-27 09:26:11	41.2184571	-1.1548868	1.94	6.52	7.47	 
    
    Args:
        track (Track): Track instance that owns these measurements
        file_content (bytes | str): RCTRK file content to parse
        
    Returns:
        int: Number of measurements successfully created
    
    Field Mapping:
        - Timestamp: Custom format (not used, too complex)
        - Time: Human-readable datetime in format YYYY-MM-DD HH:MM:SS (used for dateTime)
        - Latitude: Decimal degrees
        - Longitude: Decimal degrees
        - Accuracy: GPS accuracy in meters
        - DoseRate: Radiation dose rate in μSv/h (stored as dose_rate / 100)
        - CountRate: Count rate in CPS (stored as cpm * 60)
        - Speed: Calculated from distance/time between points (m/s)
        - Comment: Ignored
    
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
    
    lines = file_content.strip().split('\n')
    
    if len(lines) < 3:
        raise ValueError("Invalid RCTRK file: too few lines")
    
    # Skip line 1 (Track info) and line 2 (headers)
    # Parse data starting from line 3
    measurements = []
    timestamps = []
    
    for line in lines[2:]:
        # Skip empty lines
        if not line.strip():
            continue
        
        # Split by tab
        parts = line.split('\t')
        
        if len(parts) < 7:
            continue  # Skip invalid rows
        
        try:
            # Parse timestamp from the Time field (human-readable datetime)
            # The Timestamp field uses a custom format, so we use Time instead
            time_str = parts[1].strip()  # "2025-08-27 09:26:11"
            
            try:
                dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                # Make timezone-aware
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
            except ValueError:
                # Fallback: try parsing as ISO format
                dt = parse_datetime(time_str)
                if dt and timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
                elif not dt:
                    dt = timezone.now()
            
            timestamps.append(dt)
            
            # Parse fields
            latitude = float(parts[2])
            longitude = float(parts[3])
            accuracy = float(parts[4]) if parts[4].strip() else None
            dose_rate_raw = float(parts[5])  # Raw value from RCTRK
            dose_rate = dose_rate_raw / 100  # Convert to μSv/h (RCTRK uses centiSv/h or similar)
            count_rate = float(parts[6])  # CPS (counts per second)
            
            # Convert CPS to CPM
            cpm = int(count_rate * 60)
            
            # Create measurement object (not saved yet, speed will be calculated later)
            # Inherit hierarchy from track: project, mission, campaign, user
            measurement = RadiationMeasurement(
                user=track.created_by,
                device=track.device,
                project=track.project,
                campaign=track.campaign,
                track=track,
                latitude=latitude,
                longitude=longitude,
                accuracy=accuracy,
                dose_rate=dose_rate,
                radiation_unit="μSv/h",
                cpm=cpm,
                speed=None,  # Will be calculated after all measurements are created
                dateTime=dt
            )
            measurements.append(measurement)
            
        except (ValueError, IndexError) as e:
            # Skip rows with parsing errors
            continue
    
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
    
    # Phase 8: Create WeatherCache entries and associate measurements
    print(f"Phase 8/8: Creating weather cache entries...")
    from .models import WeatherCache
    
    # Group measurements by (H3 cell, hour in UTC)
    weather_groups = {}
    for measurement in measurements:
        try:
            # Calculate H3 cell (resolution 8 = ~5.5km hexagons)
            h3_cell = h3.latlng_to_cell(
                float(measurement.latitude), 
                float(measurement.longitude), 
                8
            )
            
            # Convert to UTC and round timestamp to hour (minute=0, second=0)
            # measurement.dateTime might be naive (local time), make it UTC-aware
            dt_utc = measurement.dateTime
            if dt_utc.tzinfo is None:
                # Assume local time, convert to UTC (you may need to adjust timezone)
                dt_utc = timezone.make_aware(dt_utc, timezone.utc)
            else:
                # Already timezone-aware, convert to UTC
                dt_utc = dt_utc.astimezone(timezone.utc)
            
            hour_timestamp = dt_utc.replace(minute=0, second=0, microsecond=0)
            
            # Unique key: (h3_cell, hour in UTC)
            key = (h3_cell, hour_timestamp)
            
            if key not in weather_groups:
                weather_groups[key] = {
                    'h3_cell': h3_cell,
                    'timestamp_hour': hour_timestamp,
                    'latitude': measurement.latitude,  # Use first measurement as representative
                    'longitude': measurement.longitude,
                    'measurement_ids': []
                }
            
            weather_groups[key]['measurement_ids'].append(measurement.id)
        except Exception as e:
            print(f"  Warning: Could not process H3 for measurement {measurement.id}: {e}")
            continue
    
    print(f"  Found {len(weather_groups)} unique H3+time combinations (reduced from {len(measurements)} measurements)")
    
    # Create WeatherCache entries (without weather data yet, just structure)
    weather_cache_mapping = {}
    for group_data in weather_groups.values():
        cache, created = WeatherCache.objects.get_or_create(
            h3_cell=group_data['h3_cell'],
            timestamp_hour=group_data['timestamp_hour'],
            defaults={
                'latitude': group_data['latitude'],
                'longitude': group_data['longitude'],
                'fetched': False,
                'fetch_attempts': 0
            }
        )
        weather_cache_mapping[cache.id] = group_data['measurement_ids']
        if created:
            print(f"  Created new WeatherCache: {cache.h3_cell} @ {cache.timestamp_hour}")
    
    # Associate measurements with WeatherCache (bulk update by cache)
    from .models import RadiationMeasurement
    for cache_id, measurement_ids in weather_cache_mapping.items():
        RadiationMeasurement.objects.filter(id__in=measurement_ids).update(weather_cache_id=cache_id)
    
    print(f"  Associated {len(measurements)} measurements with {len(weather_groups)} weather cache entries")
    print(f"  Weather data will be fetched by scheduler task (fetch_pending_weather)")
    
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
    from .models import RadiationMeasurement, LightPollutionMeasurement, WeatherCache
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
                cpm_error = point.get('cpmErr')
                dose_rate = point.get('doseMicroSvPerHour')
                dose_rate_error = point.get('doseMicroSvPerHourErr')
                measurement = RadiationMeasurement(
                    user=track.created_by,
                    device=track.device,
                    project=track.project,
                    campaign=track.campaign,
                    track=track,
                    latitude=latitude,
                    longitude=longitude,
                    altitude=altitude,
                    accuracy=accuracy,
                    dose_rate=dose_rate,
                    dose_rate_error=dose_rate_error,
                    radiation_unit="μSv/h",
                    cpm=int(cpm) if cpm is not None else None,
                    cpm_error=cpm_error,
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
    
    # Phase 3: Calculate speed (same logic as RCTRK) for radiation tracks
    if track_type != 'light':
        print(f"Phase 3: Calculating speeds...")
        WINDOW_SIZE = 5
        MAX_ACCURACY_FOR_SPEED = 15.0
        
        for i, measurement in enumerate(measurements):
            window_start = max(0, i - WINDOW_SIZE // 2)
            window_end = min(len(measurements), i + WINDOW_SIZE // 2 + 1)
            
            window_points = []
            for m in measurements[window_start:window_end]:
                if m.accuracy is not None and m.accuracy < MAX_ACCURACY_FOR_SPEED:
                    window_points.append(m)
            
            if len(window_points) < 2:
                continue
            
            first_point = window_points[0]
            last_point = window_points[-1]
            
            total_distance = 0
            for j in range(len(window_points) - 1):
                p1 = window_points[j]
                p2 = window_points[j + 1]
                total_distance += haversine_distance(
                    p1.latitude, p1.longitude,
                    p2.latitude, p2.longitude
                )
            
            time_diff = (last_point.dateTime - first_point.dateTime).total_seconds()
            
            if time_diff > 0:
                speed = total_distance / time_diff
                if speed < 50.0:  # Sanity check
                    measurement.speed = speed
        
        # Filter by acceleration
        MAX_ACCELERATION = 10.0
        for i, measurement in enumerate(measurements):
            if measurement.speed is None:
                continue
            
            prev_with_speed = None
            for j in range(i - 1, -1, -1):
                if measurements[j].speed is not None:
                    prev_with_speed = measurements[j]
                    break
            
            if prev_with_speed:
                speed_diff = abs(measurement.speed - prev_with_speed.speed)
                time_diff = (measurement.dateTime - prev_with_speed.dateTime).total_seconds()
                
                if time_diff > 0:
                    acceleration = speed_diff / time_diff
                    if acceleration > MAX_ACCELERATION:
                        measurement.speed = None
        
        # Bulk update speeds
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
    
    # Phase 5: Create WeatherCache entries
    print(f"Phase 5: Creating weather cache entries...")
    weather_groups = {}
    
    for measurement in measurements:
        try:
            h3_cell = h3.latlng_to_cell(
                float(measurement.latitude),
                float(measurement.longitude),
                8
            )
            
            dt_utc = measurement.dateTime
            if dt_utc.tzinfo is None:
                dt_utc = timezone.make_aware(dt_utc, timezone.utc)
            else:
                dt_utc = dt_utc.astimezone(timezone.utc)
            
            timestamp_hour = dt_utc.replace(minute=0, second=0, microsecond=0)
            key = (h3_cell, timestamp_hour)
            
            if key not in weather_groups:
                weather_groups[key] = []
            weather_groups[key].append(measurement)
            
        except Exception as e:
            print(f"  Warning: Could not calculate H3 cell for measurement: {e}")
            continue
    
    print(f"  Found {len(weather_groups)} unique H3 cell + hour combinations")
    
    # Create or get WeatherCache entries
    for (h3_cell, timestamp_hour), group_measurements in weather_groups.items():
        representative = group_measurements[0]
        
        cache, created = WeatherCache.objects.get_or_create(
            h3_cell=h3_cell,
            timestamp_hour=timestamp_hour,
            defaults={
                'latitude': representative.latitude,
                'longitude': representative.longitude,
                'fetched': False,
                'fetch_attempts': 0
            }
        )
        
        for measurement in group_measurements:
            measurement.weather_cache = cache
    
    # Bulk update weather_cache associations
    if track_type == 'light':
        LightPollutionMeasurement.objects.bulk_update(measurements, ['weather_cache'], batch_size=1000)
    else:
        RadiationMeasurement.objects.bulk_update(measurements, ['weather_cache'], batch_size=1000)
    
    print(f"  Associated {len(measurements)} measurements with {len(weather_groups)} weather cache entries")
    print(f"  Weather data will be fetched by scheduler task")
    
    return len(measurements)


def parse_gpx_track(track, file_content):
    """
    Parse GPX file and create measurements
    TODO: Implement GPX parsing
    """
    raise NotImplementedError("GPX parsing not yet implemented")


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
