from django.db import models
from django.contrib.auth.models import User  # Assuming you're using Django's User model for owners

class DeviceModel(models.Model):
    """
    Represents the different models of devices used for gamma radiation measurement.
    """
    name = models.CharField(max_length=100)  # Name of the device model
    manufacturer = models.CharField(max_length=100)  # Manufacturer of the device
    version = models.CharField(max_length=50, blank=True, null=True)  # Optional version of the device model
    max_radiation_range = models.FloatField(help_text="Maximum radiation range the device can measure (in appropriate units)")

    def __str__(self):
        return f"{self.name} (by {self.manufacturer})"

class Device(models.Model):
    """
    Represents an individual gamma radiation measurement device.
    """
    device_model = models.ForeignKey(DeviceModel, on_delete=models.CASCADE)  # Link to the DeviceModel
    serial_number = models.CharField(max_length=100, unique=True)  # Unique identifier for the device (serial number)
    hash = models.CharField(max_length=64, unique=True)  # Unique hash for the device (for identification)
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)  # Owner of the device
    purchase_date = models.DateField(blank=True, null=True)  # Optional field for when the device was purchased
    calibration_date = models.DateField(blank=True, null=True)  # Date of the last calibration
    is_active = models.BooleanField(default=True)  # Indicates if the device is still in use

    def __str__(self):
        return f"Device {self.serial_number} ({self.device_model.name})"

    class Meta:
        verbose_name = "Device"
        verbose_name_plural = "Devices"
        ordering = ['serial_number']
