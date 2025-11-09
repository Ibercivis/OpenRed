"""
Django admin configuration for the measures app.

This module configures the admin interface for viewing and managing
radiation measurements, light pollution measurements, and track files.

Admin Classes:
    - RadiationMeasurementAdmin: Gamma radiation measurement management
    - LightPollutionMeasurementAdmin: Light pollution measurement management
    - TrackAdmin: Track file management with measurement counts
    - WeatherCacheAdmin: Weather cache management
"""
from django.contrib import admin
from django.utils.html import format_html
from .models import RadiationMeasurement, LightPollutionMeasurement, Track, WeatherCache

@admin.register(RadiationMeasurement)
class RadiationMeasurementAdmin(admin.ModelAdmin):
    """
    Django admin interface for RadiationMeasurement model.
    
    Provides a comprehensive interface for viewing and editing gamma radiation
    measurements with organized fieldsets and filtering capabilities.
    
    Features:
        - List view with key fields (device, project, track, date, radiation value, location)
        - Filtering by project, device, track, radiation unit, quality, weather status
        - Search by device name, project name, track name, notes, measurement ID
        - Organized fieldsets: Basic Info, Location, Radiation Data, Calibration, Weather
        - Color-coded weather status indicator
        - Date hierarchy navigation
    
    Display Fields:
        device, project, track, dateTime, dose_rate, radiation_unit,
        latitude, longitude, weather_status (custom method)
    
    Filter Options:
        project, device, track, radiation_unit, data_quality, weather_fetched, dateTime
    
    Search Fields:
        device name, project name, track name, notes, measurement_id
    
    Methods:
        weather_status(obj): Display color-coded weather fetch status
            - Green ✓: Weather data successfully retrieved
            - Orange ⚠: Fetch attempts but not successful
            - Gray: Pending (no attempts yet)
        get_track(obj): Display track name or "Sin track"
    """
    list_display = (
        'device', 
        'project', 
        'get_track',
        'dateTime', 
        'dose_rate', 
        'radiation_unit',
        'speed',
        'latitude', 
        'longitude', 
        'weather_status'
    )
    list_filter = ('project', 'device', 'track', 'radiation_unit', 'data_quality', 'dateTime')
    search_fields = ('device__name', 'project__name', 'track__name', 'notes', 'measurement_id')
    readonly_fields = ('measurement_id', 'timestamp', 'created_at')
    date_hierarchy = 'dateTime'
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('device', 'project', 'track', 'user', 'dateTime', 'measurement_id')
        }),
        ('Ubicación', {
            'fields': ('latitude', 'longitude', 'altitude', 'accuracy')
        }),
        ('Datos de Radiación', {
            'fields': ('dose_rate', 'radiation_unit', 'cpm', 'detector_type', 'speed')
        }),
        ('Calibración y Calidad', {
            'fields': ('calibration_factor', 'background_subtracted', 'data_quality'),
            'classes': ('collapse',)
        }),
        ('Datos Meteorológicos', {
            'fields': ('weather_cache',),
            'classes': ('collapse',)
        }),
        ('Datos Adicionales', {
            'fields': ('raw_data', 'notes'),
            'classes': ('collapse',)
        })
    )
    
    def get_track(self, obj):
        """
        Display track name or default text.
        
        Args:
            obj: RadiationMeasurement instance
            
        Returns:
            str: Track name or "Sin track"
        """
        if obj.track:
            return format_html(
                '<a href="/admin/measures/track/{}/change/">{}</a>',
                obj.track.id,
                obj.track.name
            )
        return format_html('<span style="color: gray;">Sin track</span>')
    get_track.short_description = 'Track'
    
    def weather_status(self, obj):
        if obj.weather_cache_id:
            if obj.weather_cache.fetched:
                return format_html('<span style="color: green;">✓ Obtenido</span>')
            elif obj.weather_cache.fetch_attempts > 0:
                return format_html(f'<span style="color: orange;">⚠ {obj.weather_cache.fetch_attempts} intentos</span>')
        return format_html('<span style="color: gray;">Pendiente</span>')
    weather_status.short_description = 'Estado Clima'

@admin.register(LightPollutionMeasurement)
class LightPollutionMeasurementAdmin(admin.ModelAdmin):
    """
    Django admin interface for LightPollutionMeasurement model.
    
    Provides an interface for managing light pollution measurements collected
    from citizen science campaigns and research projects.
    
    Features:
        - List view with key fields (device, project, track, date, brightness, location)
        - Filtering by project, device, track, Bortle class
        - Search by device name, project name, track name, measurement ID
        - Date hierarchy navigation
        - Organized fieldsets for basic info, location, and light pollution data
    
    Display Fields:
        device, project, track, dateTime, sky_brightness, bortle_class,
        latitude, longitude
    
    Filter Options:
        project, device, track, bortle_class, dateTime
    
    Search Fields:
        device name, project name, track name, measurement_id
    
    Methods:
        get_track(obj): Display track name or "Sin track"
    """
    list_display = ('device', 'project', 'get_track', 'dateTime', 'sky_brightness', 'bortle_class', 'latitude', 'longitude')
    list_filter = ('project', 'device', 'track', 'bortle_class', 'dateTime')
    search_fields = ('device__name', 'project__name', 'track__name', 'measurement_id')
    readonly_fields = ('measurement_id', 'timestamp', 'created_at')
    date_hierarchy = 'dateTime'
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('device', 'project', 'track', 'user', 'dateTime', 'measurement_id')
        }),
        ('Ubicación', {
            'fields': ('latitude', 'longitude', 'altitude', 'accuracy')
        }),
        ('Datos de Contaminación Lumínica', {
            'fields': ('sky_brightness', 'bortle_class', 'nelm', 'sqm_value')
        })
    )
    
    def get_track(self, obj):
        """
        Display track name or default text.
        
        Args:
            obj: LightPollutionMeasurement instance
            
        Returns:
            str: Track name or "Sin track"
        """
        if obj.track:
            return format_html(
                '<a href="/admin/measures/track/{}/change/">{}</a>',
                obj.track.id,
                obj.track.name
            )
        return format_html('<span style="color: gray;">Sin track</span>')
    get_track.short_description = 'Track'

@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    """
    Django admin interface for Track model.
    
    Manages uploaded track files (CSV/GPX) and displays measurement counts.
    Provides actions for updating measurement counts after batch imports.
    
    Features:
        - List view with track name, project, device, campaign, measurement count
        - Filtering by project, device, campaign, creation date
        - Search by name, description, device name, project name
        - Admin action to bulk update measurement counts
    
    Display Fields:
        name, project, device, campaign, total_measurements, created_at
    
    Filter Options:
        project, device, campaign, created_at
    
    Search Fields:
        name, description, device name, project name
    
    Actions:
        update_measurement_counts: Recalculates measurement counts for selected tracks
    
    Methods:
        update_measurement_counts(request, queryset): Admin action to update counts
            Iterates through selected tracks and calls update_measurement_count()
            on each, then displays success message with count.
    """
    list_display = ('name', 'project', 'device', 'campaign', 'total_measurements', 'total_distance', 'avg_dose_rate', 'average_speed', 'created_at')
    list_filter = ('project', 'device', 'campaign', 'created_at')
    search_fields = ('name', 'description', 'device__name', 'project__name')
    readonly_fields = (
        'total_measurements', 'total_distance', 'average_speed', 
        'min_dose_rate', 'max_dose_rate', 'avg_dose_rate', 'std_dose_rate',
        'created_at'
    )
    
    actions = ['update_measurement_counts']
    
    def update_measurement_counts(self, request, queryset):
        for track in queryset:
            track.update_measurement_count()
        self.message_user(request, f"Updated measurement counts for {queryset.count()} tracks.")
    update_measurement_counts.short_description = "Actualizar contadores de mediciones"

@admin.register(WeatherCache)
class WeatherCacheAdmin(admin.ModelAdmin):
    """
    Django admin interface for WeatherCache model.
    
    Manages cached weather data grouped by H3 spatial cells and time windows.
    """
    list_display = (
        'h3_cell',
        'timestamp_hour',
        'temperature',
        'humidity',
        'wind_speed',
        'cloud_cover',
        'fetched',
        'fetch_attempts',
        'measurement_count'
    )
    list_filter = ('fetched', 'timestamp_hour')
    search_fields = ('h3_cell',)
    readonly_fields = ('created_at', 'updated_at', 'measurement_count')
    date_hierarchy = 'timestamp_hour'
    
    fieldsets = (
        ('Identificación', {
            'fields': ('h3_cell', 'timestamp_hour', 'latitude', 'longitude')
        }),
        ('Datos Meteorológicos', {
            'fields': ('temperature', 'humidity', 'pressure', 'wind_speed', 'wind_direction', 'cloud_cover')
        }),
        ('Estado de Obtención', {
            'fields': ('fetched', 'fetch_attempts', 'last_fetch_attempt')
        }),
        ('Datos Completos', {
            'fields': ('weather_data',),
            'classes': ('collapse',)
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at', 'measurement_count'),
            'classes': ('collapse',)
        })
    )
    
    def measurement_count(self, obj):
        """Count total measurements using this weather cache entry"""
        total = (
            obj.radiationmeasurement_set.count() +
            obj.lightpollutionmeasurement_set.count()
        )
        return format_html(f'<strong>{total}</strong> mediciones')
    measurement_count.short_description = 'Mediciones'
