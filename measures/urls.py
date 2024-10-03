from rest_framework.routers import DefaultRouter
from .views import MeasurementViewSet

router = DefaultRouter()
router.register(r'measurements', MeasurementViewSet, basename='measurement')

urlpatterns = router.urls