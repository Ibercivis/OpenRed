from django.db import models
from django.contrib.gis.db import models as gis_models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
import uuid

class WeatherCache(models.Model):
    """
    Cache for weather data grouped by H3 cell and time window.
    
    This table stores unique weather data fetched from Open-Meteo API,
    grouped by H3 hexagonal cell (resolution 8 = ~5.5km) and 1-hour time windows.
    Multiple measurements in the same spatiotemporal bucket share the same weather data.
    
    This dramatically reduces API calls: instead of fetching weather for every measurement,
    we fetch once per H3 cell per hour (typically 20-50 calls per track instead of 1000+).
    
    Attributes:
        h3_cell (str): H3 cell index at resolution 8 (~5.5km hexagon)
        timestamp_hour (DateTime): Hour-rounded timestamp (minutes/seconds set to 0)
        latitude (Decimal): Representative latitude for this H3 cell
        longitude (Decimal): Representative longitude for this H3 cell
        weather_data (JSON): Complete weather response from Open-Meteo API
        temperature (float): Temperature at 2m in Celsius
        humidity (float): Relative humidity percentage
        pressure (float): Atmospheric pressure at sea level in hPa
        wind_speed (float): Wind speed at 10m in km/h (Open-Meteo format)
        wind_direction (float): Wind direction in degrees (0-360)
        cloud_cover (float): Cloud cover percentage
        fetch_attempts (int): Number of API fetch attempts
        last_fetch_attempt (DateTime): Last API call timestamp
        fetched (bool): Whether data was successfully retrieved
        created_at (DateTime): When this cache entry was created
    
    Indexes:
        - Unique constraint on (h3_cell, timestamp_hour) prevents duplicates
        - Index on fetched for querying unfetched entries
        - Index on timestamp_hour for temporal queries
    
    Example:
        >>> # Multiple measurements in same H3 cell + hour share weather
        >>> cache = WeatherCache.objects.get_or_create(
        ...     h3_cell='88283082bffffff',  # H3 index
        ...     timestamp_hour=datetime(2024, 11, 8, 14, 0, 0),  # Rounded to hour
        ...     defaults={'temperature': 15.5, 'humidity': 65, ...}
        ... )[0]
        >>> measurement.weather_cache = cache
    """
    h3_cell = models.CharField(
        max_length=20,
        verbose_name="H3 Cell",
        help_text="H3 hexagonal cell index (resolution 8 = ~5.5km)",
        db_index=True
    )
    timestamp_hour = models.DateTimeField(
        verbose_name="Timestamp (Hour)",
        help_text="Hour-rounded timestamp for time window grouping",
        db_index=True
    )
    
    # Representative coordinates for this H3 cell
    latitude = models.DecimalField(
        max_digits=10,
        decimal_places=8,
        null=True,
        blank=True,
        verbose_name="Latitude",
        help_text="Representative latitude for this H3 cell"
    )
    longitude = models.DecimalField(
        max_digits=11,
        decimal_places=8,
        null=True,
        blank=True,
        verbose_name="Longitude",
        help_text="Representative longitude for this H3 cell"
    )
    
    # Weather data fields (from Open-Meteo API)
    weather_data = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Weather Data",
        help_text="Complete weather response from Open-Meteo API"
    )
    temperature = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Temperature (°C)",
        help_text="Temperature at 2 meters (temperature_2m)"
    )
    humidity = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Relative Humidity (%)",
        help_text="Relative humidity at 2 meters (relative_humidity_2m)"
    )
    pressure = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Pressure (hPa)",
        help_text="Atmospheric pressure at sea level (pressure_msl)"
    )
    wind_speed = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Wind Speed (km/h)",
        help_text="Wind speed at 10 meters (wind_speed_10m)"
    )
    wind_direction = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Wind Direction (°)",
        help_text="Wind direction at 10 meters (wind_direction_10m)"
    )
    cloud_cover = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Cloud Cover (%)",
        help_text="Total cloud cover percentage"
    )
    rain_sum = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Rain Sum (mm)",
        help_text="Total daily precipitation sum (rain_sum)"
    )
    
    # Fetch metadata
    fetch_attempts = models.IntegerField(
        default=0,
        verbose_name="Fetch Attempts"
    )
    last_fetch_attempt = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Fetch Attempt"
    )
    fetched = models.BooleanField(
        default=False,
        verbose_name="Data Fetched",
        db_index=True
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Weather Cache"
        verbose_name_plural = "Weather Cache Entries"
        ordering = ['-timestamp_hour']
        unique_together = [['h3_cell', 'timestamp_hour']]
        indexes = [
            models.Index(fields=['h3_cell', 'timestamp_hour']),
            models.Index(fields=['fetched']),
            models.Index(fields=['timestamp_hour']),
        ]
    
    def __str__(self):
        return f"Weather {self.h3_cell} @ {self.timestamp_hour.strftime('%Y-%m-%d %H:00')}"

# Modelo base abstracto para todas las mediciones
class BaseMeasurement(models.Model):
    """
    Abstract base class with common fields for all measurements.
    
    This model provides core fields shared across different measurement types
    (radiation, light pollution, etc.) including geolocation, timestamps,
    device information, and organizational hierarchy.
    
    Hierarchy:
        Project (required) → Mission (optional) → Campaign (optional) → Measurement
        Track (optional) can contain multiple measurements
    
    Attributes:
        device (ForeignKey): Device that captured this measurement (required)
        user (ForeignKey): User who recorded this measurement (optional)
        project (ForeignKey): Parent project (required)
        campaign (ForeignKey): Optional campaign this measurement belongs to
        track (ForeignKey): Optional track (CSV/GPX file) this measurement came from
        measurement_id (UUID): Unique identifier for this measurement
        dateTime (DateTime): When the measurement was taken (from sensor)
        timestamp (int): Unix timestamp (auto-calculated from dateTime)
        latitude (Decimal): Measurement location latitude
        longitude (Decimal): Measurement location longitude
        altitude (float): Measurement altitude in meters (optional)
        accuracy (float): GPS accuracy in meters (optional)
        data_quality (str): Quality assessment (excellent/good/fair/poor)
        notes (str): Additional observations (optional)
        created_at (DateTime): When this record was saved to database
        
    Weather fields (fetched from OpenWeather API):
        weather_fetched (bool): Whether weather data has been retrieved
        weather_fetch_attempts (int): Number of fetch attempts
        weather_last_attempt (DateTime): Last fetch attempt timestamp
        weather_data (JSON): Full weather data from API
        temperature (float): Temperature in Celsius
        humidity (float): Humidity percentage
        pressure (float): Atmospheric pressure in hPa
        wind_speed (float): Wind speed in m/s
        wind_direction (float): Wind direction in degrees
        cloudiness (float): Cloud cover percentage
        weather_description (str): Weather condition description
        
    Properties:
        mission (Mission): Access parent mission through campaign (if exists)
    
    Example:
        >>> measurement = RadiationMeasurement(
        ...     project=project,
        ...     campaign=campaign,  # Optional
        ...     device=device,
        ...     latitude=40.4168,
        ...     longitude=-3.7038,
        ...     dateTime=timezone.now()
        ... )
        >>> measurement.mission  # Returns campaign.mission if campaign exists
    """
    # Basic relationships
    device = models.ForeignKey(
        'devices.Device',
        on_delete=models.CASCADE,
        verbose_name="Device"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="User"
    )
    project = models.ForeignKey(
        'missions.Project',
        on_delete=models.CASCADE,
        verbose_name="Project",
        help_text="Parent project (required)"
    )
    campaign = models.ForeignKey(
        'missions.Campaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Campaign",
        help_text="Optional campaign this measurement belongs to"
    )
    
    # Optional relationship with Track
    track = models.ForeignKey(
        'Track',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='%(class)s_set',
        verbose_name="Track",
        help_text="Track this measurement was imported from (if any)"
    )
    
    # Identificación única
    measurement_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    
    # Datos temporales
    dateTime = models.DateTimeField(verbose_name="Fecha y Hora", db_index=True)
    timestamp = models.BigIntegerField(null=True, blank=True, help_text="Unix timestamp")
    
    # Geolocalización
    latitude = models.DecimalField(
        max_digits=10, 
        decimal_places=8, 
        verbose_name="Latitud"
    )
    longitude = models.DecimalField(
        max_digits=11, 
        decimal_places=8, 
        verbose_name="Longitud"
    )
    altitude = models.FloatField(
        blank=True, 
        null=True, 
        verbose_name="Altitud (m)"
    )
    accuracy = models.FloatField(
        blank=True, 
        null=True, 
        help_text="Precisión GPS en metros"
    )
    
    # Calidad de datos
    data_quality = models.CharField(
        max_length=20, 
        default='good',
        choices=[
            ('excellent', 'Excelente'),
            ('good', 'Buena'),
            ('fair', 'Regular'),
            ('poor', 'Pobre'),
        ]
    )
    
    # Metadatos
    notes = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Notas",
        help_text="Observaciones adicionales sobre la medición"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Weather data relationship - points to cached weather entry
    weather_cache = models.ForeignKey(
        'WeatherCache',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_set',
        verbose_name="Weather Cache",
        help_text="Cached weather data for this measurement's H3 cell + time window"
    )
    
    @property
    def mission(self):
        """
        Direct access to the parent mission through the campaign.
        
        Returns:
            Mission or None: The mission this measurement belongs to (if campaign exists)
            
        Example:
            >>> measurement.mission.name if measurement.campaign else None
            'Autumn 2024 Campaign'
        """
        if self.campaign:
            return self.campaign.mission
        return None
    
    def __str__(self):
        return f"{self.__class__.__name__} by {self.device} at {self.dateTime}"
    
    def save(self, *args, **kwargs):
        """
        Override save to auto-generate Unix timestamp from dateTime.
        
        The timestamp field is automatically calculated from dateTime if not set,
        providing Unix timestamp representation for API compatibility.
        """
        # Auto-generate timestamp if not exists
        if not self.timestamp and self.dateTime:
            # Handle both datetime objects and ISO string formats
            if isinstance(self.dateTime, str):
                from django.utils.dateparse import parse_datetime
                dt = parse_datetime(self.dateTime)
                if dt:
                    self.timestamp = int(dt.timestamp())
            else:
                self.timestamp = int(self.dateTime.timestamp())
        super().save(*args, **kwargs)
    
    class Meta:
        abstract = True
        ordering = ['-dateTime']
        indexes = [
            models.Index(fields=['dateTime']),
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['project', 'dateTime']),
            models.Index(fields=['device', 'dateTime']),
            models.Index(fields=['campaign']),
            models.Index(fields=['track']),
            models.Index(fields=['weather_cache']),
        ]

class RadiationMeasurement(BaseMeasurement):
    """
    Gamma radiation measurements from environmental sensors.
    
    This model stores radiation readings collected by devices like RadiationD-Cajal,
    including dose rate (μSv/h), counts per minute (CPM), and associated metadata.
    Each measurement is linked to a device, project, and optionally a mission/campaign.
    
    Attributes:
        radiation_unit (str): Unit of measurement, typically "μSv/h" (microsieverts per hour)
        cpm (int): Counts per minute from the Geiger-Müller tube
        cpm_error (float): Absolute error of CPM measurement (optional)
        dose_rate (float): Equivalent dose rate (radiation intensity)
        dose_rate_error (float): Absolute error of dose rate measurement (optional)
        speed (float): GPS-calculated speed in m/s at time of measurement
        detector_type (str): Type of radiation detector used (e.g., "SBM-20")
        calibration_factor (float): Device-specific calibration multiplier
        background_subtracted (bool): Whether background radiation has been subtracted
        raw_data (JSON): Complete raw data from device for debugging/analysis
        location (Point): PostGIS geographic point for spatial queries (auto-generated)
        
    Relationships (inherited from BaseMeasurement):
        device (Device): Sensor that captured this measurement
        project (Project): Parent project (required)
        campaign (Campaign): Optional campaign grouping
        track (Track): Optional track file this measurement came from
        weather_cache (WeatherCache): Cached weather data for this spatiotemporal location
        
    Spatial Features:
        - PostGIS point field enables radius queries, nearest-neighbor searches
        - H3 cell association (via weather_cache) for hexagonal binning
        - Automatic location field population from latitude/longitude
        
    Example:
        >>> measurement = RadiationMeasurement.objects.create(
        ...     project=project,
        ...     device=device,
        ...     latitude=40.4168,
        ...     longitude=-3.7038,
        ...     dateTime=timezone.now(),
        ...     dose_rate=0.12,
        ...     cpm=42,
        ...     radiation_unit="μSv/h"
        ... )
        >>> # Spatial query: find measurements within 1km
        >>> nearby = RadiationMeasurement.objects.filter(
        ...     location__distance_lte=(measurement.location, 1000)
        ... )
    """
    # Datos de radiación
    radiation_unit = models.CharField(
        max_length=20, 
        default="μSv/h",
        verbose_name="Unidad",
        help_text="Unidad de medida de radiación"
    )
    
    # Datos adicionales específicos de radiación
    cpm = models.IntegerField(
        null=True, 
        blank=True, 
        verbose_name="CPM",
        help_text="Counts per minute"
    )
    cpm_error = models.FloatField(
        null=True,
        blank=True,
        verbose_name="CPM Error",
        help_text="Absolute error of CPM measurement"
    )
    dose_rate = models.FloatField(
        null=True, 
        blank=True,
        verbose_name="Tasa de Dosis",
        help_text="Tasa de dosis equivalente"
    )
    dose_rate_error = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Dose Rate Error",
        help_text="Absolute error of dose rate measurement"
    )
    
    # Speed data (calculated from GPS track)
    speed = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Speed",
        help_text="Speed in m/s calculated from GPS positions"
    )
    
    # Información del detector
    detector_type = models.CharField(
        max_length=50, 
        blank=True,
        verbose_name="Tipo de Detector"
    )
    calibration_factor = models.FloatField(
        default=1.0,
        verbose_name="Factor de Calibración"
    )
    background_subtracted = models.BooleanField(
        default=False,
        verbose_name="Fondo Sustraído",
        help_text="Si se ha sustraído la radiación de fondo"
    )
    
    # Datos adicionales en JSON (para flexibilidad)
    raw_data = models.JSONField(
        blank=True, 
        null=True,
        help_text="Datos brutos del dispositivo en formato JSON"
    )
    
    # Campo geoespacial para consultas PostGIS y H3
    location = gis_models.PointField(
        geography=True, 
        srid=4326, 
        null=True, 
        blank=True,
        verbose_name="Ubicación Geográfica",
        help_text="Punto geográfico para consultas espaciales (auto-generado desde lat/lng)"
    )
    
    def save(self, *args, **kwargs):
        """
        Auto-populate location field from latitude/longitude.
        
        This ensures the PostGIS point is always in sync with lat/lng coordinates.
        """
        if self.latitude and self.longitude:
            from django.contrib.gis.geos import Point
            # Note: Point takes (longitude, latitude) - order is important!
            self.location = Point(float(self.longitude), float(self.latitude))
        super().save(*args, **kwargs)
    
    class Meta:
        verbose_name = "Medición de Radiación"
        verbose_name_plural = "Mediciones de Radiación"
        db_table = 'measures_radiation_measurement'
        indexes = [
            gis_models.Index(fields=['location']),  # Spatial index for fast queries
        ]

class LightPollutionMeasurement(BaseMeasurement):
    """
    Light sensor measurements for light pollution monitoring.

    This model is designed to match the JSON payload uploaded by the mobile client
    for `trackType="light"` points. Field names intentionally mirror the JSON keys
    (camelCase) to keep API <-> client mapping 1:1.

    Point-level attributes (as received in JSON):
        lux (float): Illuminance
        cct (float): Correlated color temperature
        cieX/cieY/cieU/cieV (float): CIE chromaticity coordinates
        duv (float): Duv
        tint (float): Tint
        mode (int): Measurement mode
        channels (JSON): Raw channel values array
        temperature (float): Sensor temperature
        batteryMv (int): Battery voltage in millivolts

    Note:
        Common fields like `dateTime`, `latitude`, `longitude`, `altitude`, and
        `accuracy` are inherited from BaseMeasurement.
        
    Relationships (inherited from BaseMeasurement):
        device (Device): Sky quality meter that captured this measurement
        project (Project): Parent project (required)
        campaign (Campaign): Optional campaign grouping (e.g., "Urban Light Mapping 2024")
        track (Track): Optional track file this measurement came from
        weather_cache (WeatherCache): Cached weather data (clouds affect readings!)
        
    Spatial Features:
        - PostGIS point field enables light pollution mapping and heatmaps
        - H3 hexagonal binning for aggregation (via weather_cache)
        - Automatic location field population from latitude/longitude
        
    Example:
        >>> measurement = LightPollutionMeasurement.objects.create(
        ...     project=project,
        ...     device=device,
        ...     latitude=40.4168,
        ...     longitude=-3.7038,
        ...     dateTime=timezone.now(),
        ...     lux=12.3,
        ...     cct=3200.0,
        ...     cieX=0.42,
        ...     cieY=0.41,
        ...     mode=3,
        ... )
    """
    # Optional legacy payload holder for previous schema / migrations
    legacy_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Legacy fields preserved during schema migrations"
    )

    # JSON-aligned point fields (camelCase)
    lux = models.FloatField(null=True, blank=True, verbose_name="Lux")
    cct = models.FloatField(null=True, blank=True, verbose_name="CCT")
    cieX = models.FloatField(null=True, blank=True, verbose_name="CIE x")
    cieY = models.FloatField(null=True, blank=True, verbose_name="CIE y")
    cieU = models.FloatField(null=True, blank=True, verbose_name="CIE u")
    cieV = models.FloatField(null=True, blank=True, verbose_name="CIE v")
    duv = models.FloatField(null=True, blank=True, verbose_name="Duv")
    tint = models.FloatField(null=True, blank=True, verbose_name="Tint")
    mode = models.IntegerField(null=True, blank=True, verbose_name="Mode")
    channels = models.JSONField(null=True, blank=True, verbose_name="Channels")
    temperature = models.FloatField(null=True, blank=True, verbose_name="Temperature")
    batteryMv = models.IntegerField(null=True, blank=True, verbose_name="Battery (mV)")
    speed = models.FloatField(null=True, blank=True, verbose_name="Speed")
    
    # Campo geoespacial para consultas PostGIS y H3
    location = gis_models.PointField(
        geography=True, 
        srid=4326, 
        null=True, 
        blank=True,
        verbose_name="Ubicación Geográfica",
        help_text="Punto geográfico para consultas espaciales (auto-generado desde lat/lng)"
    )
    
    def save(self, *args, **kwargs):
        """
        Auto-populate location field from latitude/longitude.
        
        This ensures the PostGIS point is always in sync with lat/lng coordinates.
        """
        if self.latitude and self.longitude:
            from django.contrib.gis.geos import Point
            # Note: Point takes (longitude, latitude) - order is important!
            self.location = Point(float(self.longitude), float(self.latitude))
        super().save(*args, **kwargs)
    
    class Meta:
        verbose_name = "Medición de Contaminación Lumínica"
        verbose_name_plural = "Mediciones de Contaminación Lumínica"
        db_table = 'measures_light_pollution_measurement'
        indexes = [
            gis_models.Index(fields=['location']),  # Spatial index for fast queries
        ]

class Track(models.Model):
    """
    Track or route of measurements - represents an uploaded file with multiple measurements.
    
    A Track is created when a CSV/GPX file is uploaded containing multiple measurements.
    Tracks are processed asynchronously using RQ (Redis Queue) to avoid blocking HTTP requests.
    
    Hierarchy:
        Project (required) → Mission (optional) → Campaign (optional) → Track → Measurements
    
    Attributes:
        project (ForeignKey): Parent project (required)
        device (ForeignKey): Device that captured the measurements (required)
        mission (ForeignKey): Optional mission this track belongs to
        campaign (ForeignKey): Optional campaign this track belongs to
        file (FileField): Uploaded file (CSV/GPX/JSON)
        file_type (str): Type of uploaded file (csv/gpx/json)
        description (str): Track description (optional)
        start_time (DateTime): First measurement timestamp (auto-calculated)
        end_time (DateTime): Last measurement timestamp (auto-calculated)
        total_measurements (int): Number of measurements in track (auto-calculated)
        status (str): Processing status (pending/processing/completed/failed)
        error_message (str): Error details if processing failed
        created_at (DateTime): When track was uploaded
        created_by (User): User who uploaded this track
        updated_at (DateTime): Last status update
    
    Properties:
        name (str): Track filename (read-only, derived from file)
        mission (Mission): Parent mission through campaign (if campaign exists)
    
    Processing Flow:
        1. Track created with status='pending'
        2. RQ job enqueued: process_track_file(track_id)
        3. Status changes to 'processing'
        4. CSV parsed, measurements bulk-created
        5. Status changes to 'completed' or 'failed'
    
    Example:
        >>> track = Track.objects.create(
        ...     project=project,
        ...     campaign=campaign,  # Optional
        ...     device=device,
        ...     file=uploaded_file,
        ...     file_type='csv',
        ...     status='pending'
        ... )
        >>> # RQ worker processes asynchronously
        >>> track.refresh_from_db()
        >>> track.status
        'completed'
        >>> track.total_measurements
        150
    """
    # Relationships - Project required, Mission and Campaign optional
    project = models.ForeignKey(
        'missions.Project', 
        on_delete=models.CASCADE, 
        related_name='tracks',
        verbose_name="Project",
        help_text="Parent project (required)"
    )
    device = models.ForeignKey(
        'devices.Device', 
        on_delete=models.CASCADE, 
        related_name='tracks',
        verbose_name="Device",
        help_text="Device that captured the measurements"
    )
    mission = models.ForeignKey(
        'missions.Mission',
        on_delete=models.SET_NULL,
        related_name='tracks',
        null=True,
        blank=True,
        verbose_name="Mission",
        help_text="Optional mission this track belongs to"
    )
    campaign = models.ForeignKey(
        'missions.Campaign', 
        on_delete=models.SET_NULL, 
        related_name='tracks',
        null=True,
        blank=True,
        verbose_name="Campaign",
        help_text="Optional campaign this track belongs to"
    )
    
    # Archivo subido
    file = models.FileField(
        upload_to='tracks/%Y/%m/%d/',
        null=True,
        blank=True,
        verbose_name="Archivo",
        help_text="Archivo CSV/GPX con las mediciones"
    )
    file_type = models.CharField(
        max_length=10,
        choices=[
            ('rctrk', 'RCTRK'),
            ('json', 'JSON'),
        ],
        default='rctrk',
        verbose_name="Tipo de Archivo"
    )
    
    # Metadatos del track
    description = models.TextField(blank=True, verbose_name="Descripción")
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="Hora de Inicio")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="Hora de Fin")
    total_measurements = models.IntegerField(default=0, verbose_name="Total de Mediciones")
    
    # Campos adicionales para JSON de RadiaCode
    synced = models.BooleanField(
        default=False,
        null=True,
        blank=True,
        verbose_name="Synced",
        help_text="Track synced status from mobile app"
    )
    synced_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Synced At",
        help_text="When track was synced to cloud"
    )
    cloud_track_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="Cloud Track ID",
        help_text="Track ID from cloud/mobile app"
    )
    required_gps_accuracy_meters = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Required GPS Accuracy",
        help_text="Required GPS accuracy in meters for this track"
    )
    total_distance = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Total Distance",
        help_text="Total distance traveled in meters"
    )
    average_speed = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Average Speed",
        help_text="Average speed of the track in m/s"
    )
    
    # Radiation statistics (for radiation tracks)
    min_dose_rate = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Minimum Dose Rate",
        help_text="Minimum dose rate in μSv/h"
    )
    max_dose_rate = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Maximum Dose Rate",
        help_text="Maximum dose rate in μSv/h"
    )
    avg_dose_rate = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Average Dose Rate",
        help_text="Average dose rate in μSv/h"
    )
    std_dose_rate = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Dose Rate Std Deviation",
        help_text="Standard deviation of dose rate in μSv/h"
    )
    
    # Estado del procesamiento
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('processing', 'Procesando'),
        ('completed', 'Completado'),
        ('failed', 'Fallido'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name="Estado"
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="Mensaje de Error"
    )
    
    # Auditoría
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_tracks',
        verbose_name="Creado por"
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    def update_measurement_count(self):
        """
        Update the measurement counter for this track.
        
        Counts all radiation and light pollution measurements linked to this track
        and updates the total_measurements field.
        
        Example:
            >>> track.update_measurement_count()
            >>> track.total_measurements
            150
        """
        radiation_count = self.radiationmeasurement_set.count()
        light_count = self.lightpollutionmeasurement_set.count()
        self.total_measurements = radiation_count + light_count
        self.save(update_fields=['total_measurements'])
    
    @property
    def name(self):
        """
        Get track name from uploaded filename.
        
        Returns:
            str: The filename or a default "Track {id}" if no file attached
            
        Example:
            >>> track.name
            'example_track.csv'
        """
        if self.file:
            import os
            return os.path.basename(self.file.name)
        return f"Track {self.id}"
    
    def clean(self):
        """Validate mission and campaign consistency"""
        super().clean()
        
        # If campaign is set and has a mission, track.mission must match
        if self.campaign and self.campaign.mission:
            if self.mission and self.mission != self.campaign.mission:
                raise ValidationError({
                    'mission': 'La misión del track debe coincidir con la misión de la campaña'
                })
            # Auto-set mission from campaign if not set
            if not self.mission:
                self.mission = self.campaign.mission
        
        # If mission is set, it must belong to the same project
        if self.mission and self.mission.project != self.project:
            raise ValidationError({
                'mission': 'La misión debe pertenecer al mismo proyecto que el track'
            })
        
        # If campaign is set, it must belong to the mission (if mission is set)
        if self.campaign and self.mission:
            if self.campaign.mission != self.mission:
                raise ValidationError({
                    'campaign': 'La campaña debe pertenecer a la misión seleccionada'
                })
    
    def save(self, *args, **kwargs):
        """
        Override save to update measurements when track hierarchy changes.
        
        When project, mission, or campaign are modified, all measurements
        linked to this track are updated to maintain consistency.
        """
        # Check if this is an update (has pk) and hierarchy fields changed
        if self.pk:
            try:
                old_track = Track.objects.get(pk=self.pk)
                hierarchy_changed = (
                    old_track.project_id != self.project_id or
                    old_track.mission_id != self.mission_id or
                    old_track.campaign_id != self.campaign_id
                )
                
                if hierarchy_changed:
                    # Update all measurements linked to this track
                    RadiationMeasurement.objects.filter(track=self).update(
                        project=self.project,
                        campaign=self.campaign
                    )
                    LightPollutionMeasurement.objects.filter(track=self).update(
                        project=self.project,
                        campaign=self.campaign
                    )
            except Track.DoesNotExist:
                pass
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        """
        String representation of the track.
        
        Returns:
            str: Track name with project/mission/campaign hierarchy
            
        Example:
            >>> str(track)
            'example_track.csv - Radiation Project - Summer Mission - Campaign 1'
        """
        parts = [self.name, self.project.name]
        if self.mission:
            parts.append(self.mission.name)
        if self.campaign:
            parts.append(self.campaign.name)
        return " - ".join(parts)
    
    class Meta:
        verbose_name = "Track"
        verbose_name_plural = "Tracks"
        ordering = ['-created_at']

# Para mantener compatibilidad temporal si es necesario
# Measurement = RadiationMeasurement  # Alias de compatibilidad
