"""Tests for missions API endpoints."""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import Project, Mission, Campaign


class MissionsListRequiresProjectTests(TestCase):
	def setUp(self):
		self.client = APIClient()

		self.project1 = Project.objects.create(
			name='Project 1',
			description='Test project 1',
			project_type='radiation',
		)
		self.project2 = Project.objects.create(
			name='Project 2',
			description='Test project 2',
			project_type='light_pollution',
		)

		self.mission1_p1 = Mission.objects.create(
			name='Mission 1 P1',
			description='',
			project=self.project1,
			start_date='2024-01-01',
			end_date='2024-01-02',
		)
		self.mission2_p1 = Mission.objects.create(
			name='Mission 2 P1',
			description='',
			project=self.project1,
			start_date='2024-02-01',
			end_date='2024-02-02',
		)
		self.mission1_p2 = Mission.objects.create(
			name='Mission 1 P2',
			description='',
			project=self.project2,
			start_date='2024-03-01',
			end_date='2024-03-02',
		)

		self.campaign1_m1p1 = Campaign.objects.create(
			name='Campaign 1 (m1 p1)',
			description='',
			mission=self.mission1_p1,
			start_date='2024-01-01',
			end_date='2024-01-02',
		)
		self.campaign2_m1p1 = Campaign.objects.create(
			name='Campaign 2 (m1 p1)',
			description='',
			mission=self.mission1_p1,
			start_date='2024-01-03',
			end_date='2024-01-04',
		)
		self.campaign1_m1p2 = Campaign.objects.create(
			name='Campaign 1 (m1 p2)',
			description='',
			mission=self.mission1_p2,
			start_date='2024-03-01',
			end_date='2024-03-02',
		)

	def _extract_items(self, data):
		# Support both paginated ({count, results}) and non-paginated (list) responses
		if isinstance(data, dict) and 'results' in data:
			return data['results']
		return data

	def test_list_requires_project_query_param(self):
		# Missions can only be listed under a project via nested endpoint.
		response = self.client.get('/api/missions/')
		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

	def test_nested_list_filters_by_project_path_param(self):
		response = self.client.get(f'/api/projects/{self.project1.id}/missions/')
		self.assertEqual(response.status_code, status.HTTP_200_OK)

		items = self._extract_items(response.data)
		self.assertEqual(len(items), 2)
		self.assertTrue(all(item['project'] == self.project1.id for item in items))

	def test_nested_campaigns_list_filters_by_mission_path_param(self):
		response = self.client.get(f'/api/missions/{self.mission1_p1.id}/campaigns/')
		self.assertEqual(response.status_code, status.HTTP_200_OK)

		items = self._extract_items(response.data)
		self.assertEqual(len(items), 2)
		self.assertTrue(all(item['mission'] == self.mission1_p1.id for item in items))

	def test_campaigns_list_endpoint_is_not_exposed(self):
		# Campaigns can only be listed under a mission via nested endpoint.
		response = self.client.get('/api/campaigns/?mission=1')
		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

	def test_retrieve_does_not_require_project_query_param(self):
		response = self.client.get(f'/api/missions/{self.mission1_p1.id}/')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['id'], self.mission1_p1.id)

	def test_campaign_detail_is_available(self):
		response = self.client.get(f'/api/campaigns/{self.campaign1_m1p1.id}/')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['id'], self.campaign1_m1p1.id)

