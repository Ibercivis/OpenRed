from django.db import models
from django.contrib.auth.models import User

class Project(models.Model):
    """
    Top-level organizational unit for measurement data collection.
    
    A Project defines the type of measurements (radiation or light pollution) and
    serves as the root of the organizational hierarchy. Each project can have
    multiple missions, campaigns, and measurements associated with it.
    
    Hierarchy:
        Project (you are here) → Mission → Campaign → Track/Measurements
    
    Attributes:
        name (str): Project name (e.g., "Global Radiation Monitoring")
        description (str): Detailed project description and objectives
        project_type (str): Type of measurements - 'radiation' or 'light_pollution'
        is_active (bool): Whether project accepts new data (default: True)
        is_public (bool): Whether project data is publicly visible (default: False)
        created_by (User): User who created this project (nullable)
        created_at (datetime): Project creation timestamp
        updated_at (datetime): Last modification timestamp
        project_settings (JSON): Flexible JSON field for project-specific configuration
        
    Project Types:
        - 'radiation': Gamma radiation measurements (μSv/h, CPM)
        - 'light_pollution': Sky brightness measurements (mag/arcsec²)
        
    Access Control:
        - is_public=True: Anyone can view measurements
        - is_public=False: Only project members can access data
        
    Example:
        >>> project = Project.objects.create(
        ...     name="European Radiation Survey 2024",
        ...     description="Comprehensive radiation mapping across EU",
        ...     project_type='radiation',
        ...     is_active=True,
        ...     is_public=False,
        ...     created_by=user,
        ...     project_settings={'alert_threshold': 0.5}  # Custom μSv/h threshold
        ... )
        >>> project.missions.count()
        3
        >>> project.get_project_type_display()
        'Radiación Gamma'
    
    Methods:
        __str__(): Returns "ProjectName (ProjectType)"
        get_project_type_display(): Returns human-readable project type
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

class Mission(models.Model):
    """
    Abstract mission within a project.
    
    A Mission represents an abstract organizational grouping of campaigns within
    a specific project (radiation or light pollution). Missions are thematic
    collections that help organize related data collection efforts.
    
    Hierarchy:
        Project → Mission → Campaign → Track/Measurements
    
    Attributes:
        name (str): Mission name (must be unique within the project)
        description (str): Detailed description of the mission's objectives
        project (ForeignKey): Parent project (required)
        start_date (date): Mission start date
        end_date (date): Mission end date
        created_at (datetime): Record creation timestamp
        created_by (User): User who created this mission
    
    Example:
        >>> project = Project.objects.get(project_type='radiation')
        >>> mission = Mission.objects.create(
        ...     name="Autumn 2024 Campaign",
        ...     project=project,
        ...     start_date=date(2024, 9, 1),
        ...     end_date=date(2024, 11, 30)
        ... )
    """
    name = models.CharField(
        max_length=200,
        verbose_name="Mission Name"
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description"
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='missions',
        verbose_name="Project",
        help_text="Parent project this mission belongs to"
    )
    start_date = models.DateField(
        verbose_name="Start Date"
    )
    end_date = models.DateField(
        verbose_name="End Date"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Created At"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_missions',
        verbose_name="Created By"
    )

    class Meta:
        verbose_name = "Mission"
        verbose_name_plural = "Missions"
        ordering = ['-start_date']
        unique_together = [['project', 'name']]  # Name must be unique within project

    def __str__(self):
        return f"{self.name} ({self.project.name})"

class Campaign(models.Model):
    """
    Specific data collection campaign within a mission.
    
    A Campaign represents a concrete data collection effort with defined participants,
    devices, and time boundaries. Campaigns are where actual measurements are taken.
    Multiple campaigns can belong to the same mission.
    
    Hierarchy:
        Project → Mission → Campaign → Track/Measurements
    
    Attributes:
        name (str): Campaign name (must be unique within the mission)
        description (str): Detailed description of the campaign
        mission (ForeignKey): Parent mission (required)
        participants (ManyToManyField): Users participating in this campaign
        start_date (date): Campaign start date
        end_date (date): Campaign end date
        created_at (datetime): Record creation timestamp
        created_by (User): User who created this campaign
    
    Properties:
        project (Project): Direct access to the parent project through mission
    
    Example:
        >>> mission = Mission.objects.get(name="Autumn 2024")
        >>> campaign = Campaign.objects.create(
        ...     name="Barcelona Field Study",
        ...     mission=mission,
        ...     start_date=date(2024, 9, 15),
        ...     end_date=date(2024, 9, 20)
        ... )
        >>> campaign.participants.add(user1, user2)
    """
    name = models.CharField(
        max_length=200,
        verbose_name="Campaign Name"
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description"
    )
    mission = models.ForeignKey(
        Mission,
        on_delete=models.CASCADE,
        related_name='campaigns',
        verbose_name="Mission",
        help_text="Parent mission this campaign belongs to"
    )
    participants = models.ManyToManyField(
        User,
        related_name='campaigns',
        blank=True,
        verbose_name="Participants",
        help_text="Users participating in this campaign"
    )
    start_date = models.DateField(
        verbose_name="Start Date"
    )
    end_date = models.DateField(
        verbose_name="End Date"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Created At"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_campaigns',
        verbose_name="Created By"
    )

    class Meta:
        verbose_name = "Campaign"
        verbose_name_plural = "Campaigns"
        ordering = ['-start_date']
        unique_together = [['mission', 'name']]  # Name must be unique within mission

    def __str__(self):
        return f"{self.name} ({self.mission.name})"
    
    @property
    def project(self):
        """
        Direct access to the parent project through the mission.
        
        Returns:
            Project: The project this campaign belongs to
            
        Example:
            >>> campaign.project.project_type
            'radiation'
        """
        return self.mission.project
