# devices/urls.py
from rest_framework.routers import DefaultRouter
from .views import DeviceModelViewSet, DeviceViewSet

router = DefaultRouter()
router.register(r'device-models', DeviceModelViewSet, basename='device-model')
router.register(r'devices', DeviceViewSet, basename='device')

urlpatterns = router.urls