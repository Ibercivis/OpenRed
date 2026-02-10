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
    
    Features:
        - List display with project, campaign count, and measurement count
        - Filtering by project and dates
        - Search by name and description
        - Computed fields for campaigns and measurements
    
    List Display Columns:
        - name: Mission name
        - project: Parent project
        - start_date: Start date
        - end_date: End date
        - campaign_count: Number of campaigns (computed)
        - measurement_count: Total measurements (computed)
    """
    list_display = ('name', 'project', 'start_date', 'end_date', 'campaign_count', 'measurement_count')
    list_filter = ('project', 'start_date', 'end_date', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at',)
    
    def campaign_count(self, obj):
        """
        Display the number of campaigns in this mission.
        
        Args:
            obj (Mission): The mission instance
            
        Returns:
            SafeString: HTML-formatted count display
        """
        count = obj.campaigns.count()
        return format_html(f'<strong>{count}</strong>')
    campaign_count.short_description = 'Campañas'
    
    def measurement_count(self, obj):
        """
        Display total measurement count for this mission.
        
        Counts measurements across all campaigns in this mission,
        using the appropriate measurement table based on project type.
        
        Args:
            obj (Mission): The mission instance
            
        Returns:
            SafeString: HTML-formatted count display
        """
        # Import here to avoid circular imports
        from measures.models import RadiationMeasurement, LightPollutionMeasurement
        
        # Get all campaigns in this mission
        campaign_ids = obj.campaigns.values_list('id', flat=True)
        
        # Count measurements based on project type
        if obj.project.project_type == 'radiation':
            count = RadiationMeasurement.objects.filter(campaign_id__in=campaign_ids).count()
        elif obj.project.project_type == 'light_pollution':
            count = LightPollutionMeasurement.objects.filter(campaign_id__in=campaign_ids).count()
        else:
            count = 0
        
        return format_html(f'<strong>{count}</strong>')
    measurement_count.short_description = 'Mediciones'


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    """
    Admin interface for Campaign model.
    
    Features:
        - List display with mission, project, and measurement count
        - Filtering by mission, project, and dates
        - Search by name and description
        - Computed fields for project and measurements
    
    List Display Columns:
        - name: Campaign name
        - mission: Parent mission
        - project: Parent project (computed)
        - start_date: Start date
        - end_date: End date
        - measurement_count: Total measurements (computed)
    """
    list_display = ('name', 'mission', 'get_project', 'start_date', 'end_date', 'measurement_count')
    list_filter = ('mission', 'mission__project', 'start_date', 'end_date', 'created_at')
    search_fields = ('name', 'description', 'mission__name')
    readonly_fields = ('created_at',)
    filter_horizontal = ('participants',)
    
    def get_project(self, obj):
        """
        Display the parent project through the mission.
        
        Args:
            obj (Campaign): The campaign instance
            
        Returns:
            str: Project name with type
        """
        return obj.project
    get_project.short_description = 'Proyecto'
    get_project.admin_order_field = 'mission__project'
    
    def measurement_count(self, obj):
        """
        Display total measurement count for this campaign.
        
        Counts measurements using the appropriate table based on project type.
        
        Args:
            obj (Campaign): The campaign instance
            
        Returns:
            SafeString: HTML-formatted count display
        """
        # Import here to avoid circular imports
        from measures.models import RadiationMeasurement, LightPollutionMeasurement
        
        # Count measurements based on project type
        if obj.project.project_type == 'radiation':
            count = RadiationMeasurement.objects.filter(campaign=obj).count()
        elif obj.project.project_type == 'light_pollution':
            count = LightPollutionMeasurement.objects.filter(campaign=obj).count()
        else:
            count = 0
        
        return format_html(f'<strong>{count}</strong>')
    measurement_count.short_description = 'Mediciones'