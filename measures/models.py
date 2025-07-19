from django.db import models
from django.contrib.auth.models import User
import uuid

class Project(models.Model):
    """
    Proyecto que define el tipo de mediciones y agrupa dispositivos
    """
    PROJECT_TYPES = [
        ('radiation', 'Radiación Gamma'),
        ('light_pollution', 'Contaminación Lumínica'),
    ]
    
    name = models.CharField(max_length=200, verbose_name="Nombre del Proyecto")
    description = models.TextField(blank=True, verbose_name="Descripción")
    project_type = models.CharField(
        max_length=20, 
        choices=PROJECT_TYPES,
        verbose_name="Tipo de Proyecto"
    )
    
    # Configuración del proyecto
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    is_public = models.BooleanField(default=False, verbose_name="Público")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Configuración específica por tipo de proyecto (JSON flexible)
    project_settings = models.JSONField(
        default=dict, 
        blank=True,
        help_text="Configuración específica del proyecto en formato JSON"
    )
    
    def __str__(self):
        return f"{self.name} ({self.get_project_type_display()})"
    
    class Meta:
        verbose_name = "Proyecto"
        verbose_name_plural = "Proyectos"
        ordering = ['name']

# Modelo base abstracto para todas las mediciones
class BaseMeasurement(models.Model):
    """
    Clase abstracta base con campos comunes a todas las mediciones
    """
    # Relaciones básicas
    device = models.ForeignKey('devices.Device', on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    
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
    
    # Datos meteorológicos (comunes a todos los tipos)
    weather_fetched = models.BooleanField(default=False)
    weather_fetch_attempts = models.IntegerField(default=0)
    weather_last_attempt = models.DateTimeField(null=True, blank=True)
    weather_data = models.JSONField(
        null=True, 
        blank=True,
        help_text="Datos meteorológicos completos de OpenWeather"
    )
    
    # Campos meteorológicos específicos para consultas rápidas
    temperature = models.FloatField(null=True, blank=True, verbose_name="Temperatura (°C)")
    humidity = models.FloatField(null=True, blank=True, verbose_name="Humedad (%)")
    pressure = models.FloatField(null=True, blank=True, verbose_name="Presión (hPa)")
    wind_speed = models.FloatField(null=True, blank=True, verbose_name="Velocidad Viento (m/s)")
    wind_direction = models.FloatField(null=True, blank=True, verbose_name="Dirección Viento (°)")
    cloudiness = models.FloatField(null=True, blank=True, verbose_name="Nubosidad (%)")
    weather_description = models.CharField(max_length=100, blank=True, verbose_name="Descripción Clima")
    
    def __str__(self):
        return f"{self.__class__.__name__} by {self.device} at {self.dateTime}"
    
    def save(self, *args, **kwargs):
        # Auto-generar timestamp si no existe
        if not self.timestamp and self.dateTime:
            self.timestamp = int(self.dateTime.timestamp())
        super().save(*args, **kwargs)
    
    class Meta:
        abstract = True
        ordering = ['-dateTime']
        indexes = [
            models.Index(fields=['dateTime']),
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['weather_fetched']),
            models.Index(fields=['project', 'dateTime']),
            models.Index(fields=['device', 'dateTime']),
        ]

class RadiationMeasurement(BaseMeasurement):
    """
    Mediciones de radiación gamma
    """
    # Datos de radiación
    radiation_value = models.FloatField(
        verbose_name="Valor de Radiación",
        help_text="Valor principal de radiación"
    )
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
    dose_rate = models.FloatField(
        null=True, 
        blank=True,
        verbose_name="Tasa de Dosis",
        help_text="Tasa de dosis equivalente"
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
    
    class Meta:
        verbose_name = "Medición de Radiación"
        verbose_name_plural = "Mediciones de Radiación"
        db_table = 'measures_radiation_measurement'

class LightPollutionMeasurement(BaseMeasurement):
    """
    Mediciones de contaminación lumínica
    """
    # Datos principales de luminosidad
    sky_brightness = models.FloatField(
        verbose_name="Brillo del Cielo",
        help_text="Brillo del cielo medido"
    )
    brightness_unit = models.CharField(
        max_length=20, 
        default="mag/arcsec²",
        verbose_name="Unidad de Brillo"
    )
    
    # Mediciones específicas
    sqm_reading = models.FloatField(
        null=True, 
        blank=True,
        verbose_name="Lectura SQM",
        help_text="Sky Quality Meter reading"
    )
    nelm = models.FloatField(
        null=True, 
        blank=True,
        verbose_name="NELM",
        help_text="Naked Eye Limiting Magnitude"
    )
    bortle_class = models.IntegerField(
        null=True, 
        blank=True,
        choices=[(i, f"Clase {i}") for i in range(1, 10)],
        verbose_name="Clase Bortle",
        help_text="Escala Bortle de Calidad del Cielo (1-9)"
    )
    
    # Condiciones de observación
    moon_phase = models.FloatField(
        null=True, 
        blank=True,
        verbose_name="Fase Lunar",
        help_text="Fase lunar (0.0 = Luna nueva, 1.0 = Luna llena)"
    )
    moon_altitude = models.FloatField(
        null=True, 
        blank=True,
        verbose_name="Altitud Lunar",
        help_text="Altitud de la luna sobre el horizonte (grados)"
    )
    observation_angle = models.FloatField(
        null=True, 
        blank=True,
        verbose_name="Ángulo de Observación",
        help_text="Ángulo de observación desde el cenit (grados)"
    )
    observation_direction = models.CharField(
        max_length=10,
        blank=True,
        choices=[
            ('N', 'Norte'), ('NE', 'Noreste'), ('E', 'Este'), ('SE', 'Sureste'),
            ('S', 'Sur'), ('SW', 'Suroeste'), ('W', 'Oeste'), ('NW', 'Noroeste'),
            ('Z', 'Cenit')
        ],
        verbose_name="Dirección de Observación"
    )
    
    # Datos adicionales en JSON
    measurement_data = models.JSONField(
        blank=True, 
        null=True,
        help_text="Datos adicionales específicos de contaminación lumínica"
    )
    
    class Meta:
        verbose_name = "Medición de Contaminación Lumínica"
        verbose_name_plural = "Mediciones de Contaminación Lumínica"
        db_table = 'measures_light_pollution_measurement'

class Track(models.Model):
    """
    Rastro o ruta de mediciones
    """
    name = models.CharField(max_length=100, verbose_name="Nombre del Track")
    project = models.ForeignKey(
        Project, 
        on_delete=models.CASCADE, 
        related_name='tracks',
        verbose_name="Proyecto"
    )
    device = models.ForeignKey(
        'devices.Device', 
        on_delete=models.CASCADE, 
        related_name='tracks',
        verbose_name="Dispositivo"
    )
    campaign = models.ForeignKey(
        'missions.Campaign', 
        on_delete=models.CASCADE, 
        related_name='tracks',
        verbose_name="Campaña"
    )
    
    # Metadatos del track
    description = models.TextField(blank=True, verbose_name="Descripción")
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="Hora de Inicio")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="Hora de Fin")
    total_measurements = models.IntegerField(default=0, verbose_name="Total de Mediciones")
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    def update_measurement_count(self):
        """Actualiza el contador de mediciones del track"""
        radiation_count = RadiationMeasurement.objects.filter(
            device=self.device,
            dateTime__gte=self.start_time,
            dateTime__lte=self.end_time
        ).count() if self.start_time and self.end_time else 0
        
        light_count = LightPollutionMeasurement.objects.filter(
            device=self.device,
            dateTime__gte=self.start_time,
            dateTime__lte=self.end_time
        ).count() if self.start_time and self.end_time else 0
        
        self.total_measurements = radiation_count + light_count
        self.save(update_fields=['total_measurements'])
    
    def __str__(self):
        return f"Track {self.name} - {self.project.name}"
    
    class Meta:
        verbose_name = "Track"
        verbose_name_plural = "Tracks"
        ordering = ['-created_at']

# Para mantener compatibilidad temporal si es necesario
# Measurement = RadiationMeasurement  # Alias de compatibilidad
