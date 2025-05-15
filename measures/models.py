from django.db import models
from django.contrib.auth.models import User
from devices.models import Device
import uuid

class Measurement(models.Model):
    """
    Represents a measurement of gamma radiation performed by a device.
    """
    device = models.ForeignKey('devices.Device', on_delete=models.CASCADE)  # Link to the device used for the measurement
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)  # User who performed the measurement
    latitude = models.FloatField()  # Latitude of the measurement
    longitude = models.FloatField()  # Longitude of the measurement
    altitude = models.FloatField(blank=True, null=True)  # Optional: Altitude of the measurement (if relevant)
    values = models.JSONField()  # JSON field to store radiation measurement values
    dateTime = models.DateTimeField()  # Time the measurement was taken
    accuracy = models.FloatField(blank=True, null=True, help_text="Accuracy of the measurement")  # Optional: Accuracy of the measurement
    unit = models.CharField(max_length=20, blank=True, null=True, help_text="Unit of measurement (e.g., Sieverts)")  # Optional: Unit of measurement
    notes = models.TextField(blank=True, null=True, help_text="Optional notes or comments on the measurement")  # Optional: Additional notes
    weather = models.JSONField(blank=True, null=True, help_text="Weather conditions during the measurement (if available)")  # Optional: Weather info
    measurement_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)  # Unique ID for tracking the measurement

    def __str__(self):
        return f"Measurement by {self.device} at {self.dateTime}"

    class Meta:
        verbose_name = "Measurement"
        verbose_name_plural = "Measurements"
        ordering = ['-dateTime']

class Track(models.Model):
    name = models.CharField(max_length=100)
    device = models.ForeignKey('devices.Device', on_delete=models.CASCADE, related_name='tracks')
    campaign = models.ForeignKey('missions.Campaign', on_delete=models.CASCADE, related_name='tracks')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Track {self.name} - {self.device} - {self.campaign}"
