"""
Device management models for OpenRed.

This module defines models for managing measurement devices and their specifications.
Includes device models (specifications) and individual device instances with ownership.

Models:
    - DeviceModel: Device specifications and technical details
    - Device: Individual device instances with serial numbers and ownership

Each measurement in OpenRed must be associated with a registered Device.
"""
from django.db import models
from django.contrib.auth.models import User  # Assuming you're using Django's User model for owners
import hashlib

class DeviceModel(models.Model):
    """
    Represents device specifications and technical characteristics.
    
    Stores information about different models of measurement devices,
    including manufacturer details, technical specifications, and validation status.
    
    Attributes:
        name (str): Device model name (e.g., "GammaScout Standard")
        manufacturer (str): Manufacturer name (e.g., "International Medcom")
        version (str, optional): Model version or revision number
        technology (str, optional): Measurement technology used (e.g., "Geiger-Müller tube")
        validatedByOpenRed (bool): Whether OpenRed has validated this device model
        description (str, optional): Detailed description of the device
        picture (ImageField, optional): Photo of the device
        max_radiation_range (float): Maximum measurable radiation (in appropriate units)
    
    Example:
        >>> device_model = DeviceModel.objects.create(
        ...     name="GammaScout Standard",
        ...     manufacturer="International Medcom",
        ...     technology="Geiger-Müller tube",
        ...     max_radiation_range=1000.0,
        ...     validatedByOpenRed=True
        ... )
    
    Validation:
        validatedByOpenRed indicates whether OpenRed has tested and approved
        this device model for accurate measurements. Validated devices provide
        more reliable data.
    """
    name = models.CharField(max_length=100)  # Name of the device model
    manufacturer = models.CharField(max_length=100)  # Manufacturer of the device
    version = models.CharField(max_length=50, blank=True, null=True)  # Optional version of the device model
    technology = models.CharField(max_length=100, blank=True, null=True)  # Optional technology used in the device
    validatedByOpenRed = models.BooleanField(default=False)  # Indicates if the device model has been validated by OpenRed
    description = models.TextField(blank=True, null=True)  # Optional description of the device
    picture = models.ImageField(upload_to='device_pictures/', blank=True, null=True)  # Optional picture of the device
    max_radiation_range = models.FloatField(help_text="Maximum radiation range the device can measure (in appropriate units)")

    def __str__(self):
        """String representation showing device model name and manufacturer."""
        return f"{self.name} (by {self.manufacturer})"

class Device(models.Model):
    """
    Represents an individual measurement device instance.
    
    Each device has a unique serial number and belongs to a specific user.
    Devices are linked to measurements and must reference a DeviceModel
    for technical specifications.
    
    Attributes:
        device_model (ForeignKey): Link to DeviceModel for specifications
        serial_number (str): Unique device identifier (max 100 chars)
        hash (str): Auto-generated MD5 hash of serial number for quick lookup
        owner (ForeignKey): User who owns this device (nullable)
        purchase_date (date, optional): When device was purchased
        calibration_date (date, optional): Last calibration date
        is_active (bool): Whether device is currently in use (default: True)
    
    The hash field is automatically generated from the serial_number on save
    and provides a consistent identifier for API lookups.
    
    Example:
        >>> device = Device.objects.create(
        ...     device_model=gamma_scout_model,
        ...     serial_number="GS-12345",
        ...     owner=user,
        ...     calibration_date="2024-01-15",
        ...     is_active=True
        ... )
        >>> device.hash  # Auto-generated: 'a1b2c3d4e5f6...'
    
    Methods:
        save(*args, **kwargs): Overridden to auto-generate hash from serial_number
        __str__(): Returns human-readable device description
    
    Ordering:
        Devices are ordered by serial_number for consistent display.
    
    Note:
        Each measurement in OpenRed must reference a Device. This ensures
        traceability and allows filtering measurements by equipment.
    """
    device_model = models.ForeignKey(DeviceModel, on_delete=models.CASCADE)  # Link to the DeviceModel
    serial_number = models.CharField(max_length=100, unique=True)  # Unique identifier for the device (serial number)
    hash = models.CharField(max_length=64, unique=True, blank=True)  # Unique hash for the device (for identification)
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)  # Owner of the device
    purchase_date = models.DateField(blank=True, null=True)  # Optional field for when the device was purchased
    calibration_date = models.DateField(blank=True, null=True)  # Date of the last calibration
    is_active = models.BooleanField(default=True)  # Indicates if the device is still in use

    # To automatically generate a hash for the device
    def save(self, *args, **kwargs):
        """
        Override save to auto-generate device hash.
        
        Creates MD5 hash of serial_number to provide a consistent
        identifier for API lookups and device identification.
        
        Args:
            *args: Positional arguments passed to parent save()
            **kwargs: Keyword arguments passed to parent save()
        
        Note:
            Hash is regenerated on every save, so changing serial_number
            will update the hash accordingly.
        """
        # Generate a unique hash based on the device serial number
        self.hash = hashlib.md5(self.serial_number.encode('utf-8')).hexdigest()
        super(Device, self).save(*args, **kwargs)

    def __str__(self):
        """
        String representation showing device details.
        
        Returns:
            str: Format "Device {serial} ({model}) - Owner: {owner}"
        
        Example:
            "Device GS-12345 (GammaScout Standard) - Owner: john"
            "Device SQM-67890 (Sky Quality Meter) - Owner: No owner"
        """
        return f"Device {self.serial_number} ({self.device_model.name}) - Owner: {self.owner.username if self.owner else 'No owner'}"

    class Meta:
        verbose_name = "Device"
        verbose_name_plural = "Devices"
        ordering = ['serial_number']
