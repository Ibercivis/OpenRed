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

class Campaign(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name='campaigns')
    participants = models.ManyToManyField(User, related_name='campaigns')
    devices = models.ManyToManyField('devices.Device', related_name='campaigns')  # Asume modelo Device en app 'devices'
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.mission.name})"