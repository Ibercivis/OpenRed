# missions/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import ProjectViewSet, MissionViewSet, CampaignViewSet

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='project')

urlpatterns = [
	path(
		'projects/<int:project_pk>/missions/',
		MissionViewSet.as_view({'get': 'list'}),
		name='project-missions-list',
	),
	path(
		'missions/<int:mission_pk>/campaigns/',
		CampaignViewSet.as_view({'get': 'list'}),
		name='mission-campaigns-list',
	),
	path(
		'missions/<int:pk>/',
		MissionViewSet.as_view({'get': 'retrieve'}),
		name='mission-detail',
	),
	path(
		'campaigns/<int:pk>/',
		CampaignViewSet.as_view({'get': 'retrieve'}),
		name='campaign-detail',
	),
] + router.urls