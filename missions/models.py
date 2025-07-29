from django.db import models
from django.contrib.auth.models import User  # Assuming you're using Django's User model for owners
import hashlib

class Mission(models.Model):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class UserGroup(models.Model):
    """Modelo para agrupar usuarios"""
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    users = models.ManyToManyField(User, related_name='user_groups')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_groups')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.users.count()} usuarios)"

    def get_user_count(self):
        return self.users.count()

class Campaign(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name='campaigns')
    participants = models.ManyToManyField(User, related_name='campaigns', blank=True)
    user_groups = models.ManyToManyField(UserGroup, related_name='campaigns', blank=True)
    devices = models.ManyToManyField('devices.Device', related_name='campaigns')  # Asume modelo Device en app 'devices'
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.mission.name})"

    def get_all_participants(self):
        """Obtiene todos los participantes incluyendo los de los grupos"""
        # Usar Q objects para combinar las consultas correctamente
        from django.db.models import Q
        
        participant_ids = set(self.participants.values_list('id', flat=True))
        group_participant_ids = set(
            User.objects.filter(user_groups__in=self.user_groups.all()).values_list('id', flat=True)
        )
        
        all_participant_ids = participant_ids.union(group_participant_ids)
        return User.objects.filter(id__in=all_participant_ids)

    def get_total_participants_count(self):
        """Cuenta total de participantes únicos"""
        return self.get_all_participants().count()