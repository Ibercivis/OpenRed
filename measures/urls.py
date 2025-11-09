from rest_framework.routers import DefaultRouter
from .views import (
    RadiationMeasurementViewSet,
    LightPollutionMeasurementViewSet,
    TrackViewSet,
    MeasurementViewSet  # Para compatibilidad temporal
)
from .views_stats import StatsViewSet

router = DefaultRouter()
router.register(r'radiation-measurements', RadiationMeasurementViewSet, basename='radiation-measurement')
router.register(r'light-pollution-measurements', LightPollutionMeasurementViewSet, basename='light-pollution-measurement')
router.register(r'tracks', TrackViewSet, basename='track')
router.register(r'stats', StatsViewSet, basename='stats')
# Mantener endpoint original para compatibilidad con el frontend
router.register(r'measurements', MeasurementViewSet, basename='measurement')

urlpatterns = router.urls