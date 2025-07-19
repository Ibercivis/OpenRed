from django.contrib import admin
from django.utils.html import format_html
from .models import Project, RadiationMeasurement, LightPollutionMeasurement, Track

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'project_type', 'is_active', 'is_public', 'created_at', 'measurement_count')
    list_filter = ('project_type', 'is_active', 'is_public', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('name', 'description', 'project_type')
        }),
        ('Configuración', {
            'fields': ('is_active', 'is_public', 'created_by', 'project_settings')
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def measurement_count(self, obj):
        if obj.project_type == 'radiation':
            count = RadiationMeasurement.objects.filter(project=obj).count()
        elif obj.project_type == 'light_pollution':
            count = LightPollutionMeasurement.objects.filter(project=obj).count()
        else:
            count = 0
        return format_html(f'<strong>{count}</strong>')
    measurement_count.short_description = 'Mediciones'

@admin.register(RadiationMeasurement)
class RadiationMeasurementAdmin(admin.ModelAdmin):
    list_display = ('device', 'project', 'dateTime', 'radiation_value', 'radiation_unit', 'latitude', 'longitude', 'weather_status')
    list_filter = ('project', 'device', 'radiation_unit', 'data_quality', 'weather_fetched', 'dateTime')
    search_fields = ('device__name', 'project__name', 'notes', 'measurement_id')
    readonly_fields = ('measurement_id', 'timestamp', 'created_at')
    date_hierarchy = 'dateTime'
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('device', 'project', 'user', 'dateTime', 'measurement_id')
        }),
        ('Ubicación', {
            'fields': ('latitude', 'longitude', 'altitude', 'accuracy')
        }),
        ('Datos de Radiación', {
            'fields': ('radiation_value', 'radiation_unit', 'cpm', 'dose_rate', 'detector_type')
        }),
        ('Calibración y Calidad', {
            'fields': ('calibration_factor', 'background_subtracted', 'data_quality'),
            'classes': ('collapse',)
        }),
        ('Datos Meteorológicos', {
            'fields': ('weather_fetched', 'temperature', 'humidity', 'pressure', 'weather_description'),
            'classes': ('collapse',)
        }),
        ('Datos Adicionales', {
            'fields': ('raw_data', 'notes'),
            'classes': ('collapse',)
        })
    )
    
    def weather_status(self, obj):
        if obj.weather_fetched:
            return format_html('<span style="color: green;">✓ Obtenido</span>')
        elif obj.weather_fetch_attempts > 0:
            return format_html(f'<span style="color: orange;">⚠ {obj.weather_fetch_attempts} intentos</span>')
        else:
            return format_html('<span style="color: gray;">Pendiente</span>')
    weather_status.short_description = 'Estado Clima'

@admin.register(LightPollutionMeasurement)
class LightPollutionMeasurementAdmin(admin.ModelAdmin):
    list_display = ('device', 'project', 'dateTime', 'sky_brightness', 'bortle_class', 'latitude', 'longitude')
    list_filter = ('project', 'device', 'brightness_unit', 'bortle_class', 'observation_direction', 'dateTime')
    search_fields = ('device__name', 'project__name', 'notes')
    readonly_fields = ('measurement_id', 'timestamp', 'created_at')
    date_hierarchy = 'dateTime'
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('device', 'project', 'user', 'dateTime', 'measurement_id')
        }),
        ('Ubicación', {
            'fields': ('latitude', 'longitude', 'altitude', 'accuracy')
        }),
        ('Datos de Luminosidad', {
            'fields': ('sky_brightness', 'brightness_unit', 'sqm_reading', 'nelm', 'bortle_class')
        }),
        ('Condiciones de Observación', {
            'fields': ('observation_angle', 'observation_direction', 'moon_phase', 'moon_altitude'),
            'classes': ('collapse',)
        }),
        ('Datos Meteorológicos', {
            'fields': ('weather_fetched', 'temperature', 'humidity', 'cloudiness'),
            'classes': ('collapse',)
        }),
        ('Datos Adicionales', {
            'fields': ('measurement_data', 'notes'),
            'classes': ('collapse',)
        })
    )

@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'device', 'campaign', 'total_measurements', 'created_at')
    list_filter = ('project', 'device', 'campaign', 'created_at')
    search_fields = ('name', 'description', 'device__name', 'project__name')
    readonly_fields = ('total_measurements', 'created_at')
    
    actions = ['update_measurement_counts']
    
    def update_measurement_counts(self, request, queryset):
        for track in queryset:
            track.update_measurement_count()
        self.message_user(request, f"Updated measurement counts for {queryset.count()} tracks.")
    update_measurement_counts.short_description = "Actualizar contadores de mediciones"
