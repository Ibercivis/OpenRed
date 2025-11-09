"""
Django admin configuration for missions app.

Provides admin interfaces for Project, Mission, and Campaign models
with customized list displays, filters, and computed fields.
"""
from django.contrib import admin
from django.utils.html import format_html
from .models import Project, Mission, Campaign


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """
    Admin interface for Project model.
    
    Features:
        - List display with type, status, and measurement count
        - Filtering by type, status, and creation date
        - Search by name and description
        - Organized fieldsets for better UX
        - Computed measurement_count field
    
    List Display Columns:
        - name: Project name
        - project_type: Type (radiation/light_pollution)
        - is_active: Active status
        - is_public: Public visibility status
        - created_at: Creation date
        - measurement_count: Total measurements (computed)
    """
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
        """
        Display total measurement count for this project.
        
        Queries the appropriate measurement table based on project_type
        and returns a formatted count with bold styling.
        
        Args:
            obj (Project): The project instance
            
        Returns:
            SafeString: HTML-formatted count display
        """
        # Import here to avoid circular imports
        from measures.models import RadiationMeasurement, LightPollutionMeasurement
        
        if obj.project_type == 'radiation':
            count = RadiationMeasurement.objects.filter(project=obj).count()
        elif obj.project_type == 'light_pollution':
            count = LightPollutionMeasurement.objects.filter(project=obj).count()
        else:
            count = 0
        return format_html(f'<strong>{count}</strong>')
    measurement_count.short_description = 'Mediciones'


@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    """
    Admin interface for Mission model.
    
    Basic admin configuration with default list display and filters.
    """
    pass


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    """
    Admin interface for Campaign model.
    
    Basic admin configuration with default list display and filters.
    Includes many-to-many relationship for participants.
    """
    pass