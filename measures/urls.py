from rest_framework.routers import DefaultRouter
from .views import (
    ProjectViewSet,
    RadiationMeasurementViewSet,
    LightPollutionMeasurementViewSet,
    TrackViewSet,
    MeasurementViewSet  # Para compatibilidad temporal
)

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'radiation-measurements', RadiationMeasurementViewSet, basename='radiation-measurement')
router.register(r'light-pollution-measurements', LightPollutionMeasurementViewSet, basename='light-pollution-measurement')
router.register(r'tracks', TrackViewSet, basename='track')
# Mantener endpoint original para compatibilidad con el frontend
router.register(r'measurements', MeasurementViewSet, basename='measurement')

urlpatterns = router.urls