# devices/urls.py
from rest_framework.routers import DefaultRouter
from .views import MissionViewSet, CampaignViewSet

router = DefaultRouter()
router.register(r'missions', MissionViewSet, basename='mission')
router.register(r'campaigns', CampaignViewSet, basename='campaign')

urlpatterns = router.urls