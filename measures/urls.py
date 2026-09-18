from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    RadiationMeasurementViewSet,
    LightPollutionMeasurementViewSet,
    TrackViewSet,
    RadiationSpectrumViewSet,
    StationViewSet,
    RadiationIngestView,
    MeasurementViewSet  # Para compatibilidad temporal
)
from .views_stats import StatsViewSet

router = DefaultRouter()
router.register(r'radiation-measurements', RadiationMeasurementViewSet, basename='radiation-measurement')
router.register(r'light-pollution-measurements', LightPollutionMeasurementViewSet, basename='light-pollution-measurement')
router.register(r'tracks', TrackViewSet, basename='track')
router.register(r'radiation-spectra', RadiationSpectrumViewSet, basename='radiation-spectrum')
router.register(r'stats', StatsViewSet, basename='stats')
router.register(r'stations', StationViewSet, basename='station')
# Mantener endpoint original para compatibilidad con el frontend
router.register(r'measurements', MeasurementViewSet, basename='measurement')

urlpatterns = [
    # Single-observation ingest for headless devices (M5Stack), auth by device token.
    path('ingest/radiation/', RadiationIngestView.as_view(), name='radiation-ingest'),
] + router.urls