
from django.apps import AppConfig
"""
This module defines the configuration for the 'measures' application.

Classes:
    MeasuresConfig(AppConfig): Configuration class for the 'measures' app.
        - default_auto_field (str): Specifies the type of auto field to use for primary keys.
        - name (str): The name of the application.
"""

class MeasuresConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'measures'
