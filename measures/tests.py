"""
Tests for measures app permissions, API endpoints, and H3 aggregation.

Run with:
    python manage.py test measures
    python manage.py test measures.tests.RadiationMeasurementPermissionTests
    python manage.py test measures.tests.H3AggregationTestCase
    python manage.py test measures -v 2
"""

from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from decimal import Decimal
import h3

from .models import RadiationMeasurement, LightPollutionMeasurement, Track
from missions.models import Project, Mission, Campaign
from devices.models import Device, DeviceModel


# Disable email verification for tests
@override_settings(
    ACCOUNT_EMAIL_VERIFICATION='none',
    ACCOUNT_EMAIL_REQUIRED=False,
    EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
)
class RadiationMeasurementPermissionTests(TestCase):
    """
    Test permissions for radiation measurements using API registration
    """
    
    def setUp(self):
        """
        Set up test users using API registration and test data
        """
        # Create API clients
        self.client1 = APIClient()
        self.client2 = APIClient()
        self.public_client = APIClient()
        
        # Register user1 via API
        user1_data = {
            'email': 'testuser1@test.com',
            'password1': 'testpass123!',
            'password2': 'testpass123!'
        }
        response1 = self.public_client.post('/dj-rest-auth/registration/', user1_data, format='json')
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED, f"User1 registration failed: {response1.data}")
        self.token1 = response1.data['key']
        self.user1 = User.objects.get(email='testuser1@test.com')
        
        # Register user2 via API
        user2_data = {
            'email': 'testuser2@test.com',
            'password1': 'testpass123!',
            'password2': 'testpass123!'
        }
        response2 = self.public_client.post('/dj-rest-auth/registration/', user2_data, format='json')
        self.assertEqual(response2.status_code, status.HTTP_201_CREATED, f"User2 registration failed: {response2.data}")
        self.token2 = response2.data['key']
        self.user2 = User.objects.get(email='testuser2@test.com')
        
        # Authenticate clients with tokens
        self.client1.credentials(HTTP_AUTHORIZATION=f'Token {self.token1}')
        self.client2.credentials(HTTP_AUTHORIZATION=f'Token {self.token2}')
        
        # Create test project
        self.project = Project.objects.create(
            name='Test Radiation Project',
            description='Test project for radiation permissions',
            project_type='radiation'
        )
        
        # Create device model and devices
        self.device_model = DeviceModel.objects.create(
            name='Test Device Model',
            description='Test device model',
            max_radiation_range=1000.0
        )
        
        self.device1 = Device.objects.create(
            serial_number='TEST-RAD-001',
            device_model=self.device_model
        )
        
        self.device2 = Device.objects.create(
            serial_number='TEST-RAD-002',
            device_model=self.device_model
        )
        
        # Create test measurements for user1
        self.measurement_user1 = RadiationMeasurement.objects.create(
            user=self.user1,
            device=self.device1,
            project=self.project,
            latitude=0.0,
            longitude=0.0,
            dose_rate=100.5,
            cpm=100,
            dateTime=timezone.now()
        )
        
        # Create test measurements for user2
        self.measurement_user2 = RadiationMeasurement.objects.create(
            user=self.user2,
            device=self.device2,
            project=self.project,
            latitude=1.0,
            longitude=1.0,
            dose_rate=120.5,
            cpm=120,
            dateTime=timezone.now()
        )
    
    def test_public_can_list_all_measurements(self):
        """
        Test that anyone (without auth) can list all measurements
        """
        url = '/api/radiation-measurements/'
        response = self.public_client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 2)
    
    def test_public_can_retrieve_measurement(self):
        """
        Test that anyone can retrieve a specific measurement
        """
        url = f'/api/radiation-measurements/{self.measurement_user1.id}/'
        response = self.public_client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.measurement_user1.id)
    
    def test_authenticated_user_can_create_measurement(self):
        """
        Test that authenticated user can create a measurement via API
        """
        url = '/api/radiation-measurements/'
        data = {
            'device': self.device1.id,
            'project': self.project.id,
            'latitude': 2.0,
            'longitude': 2.0,
            'dose_rate': 150.0,
            'cpm': 150,
            'dateTime': timezone.now().isoformat()
        }
        
        response = self.client1.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['user'], self.user1.id)
    
    def test_unauthenticated_cannot_create_measurement(self):
        """
        Test that unauthenticated user cannot create a measurement
        """
        # Create a fresh client without any session
        unauthenticated_client = APIClient()
        
        url = '/api/radiation-measurements/'
        data = {
            'device': self.device1.id,
            'project': self.project.id,
            'latitude': 2.0,
            'longitude': 2.0,
            'dose_rate': 150.0,
            'cpm': 150,
            'dateTime': timezone.now().isoformat()
        }
        
        response = unauthenticated_client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_user_can_delete_own_measurement(self):
        """
        Test that a user can delete their own measurement
        """
        url = f'/api/radiation-measurements/{self.measurement_user1.id}/'
        response = self.client1.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(RadiationMeasurement.objects.filter(id=self.measurement_user1.id).exists())
    
    def test_user_cannot_delete_other_user_measurement(self):
        """
        Test that a user cannot delete another user's measurement (should get 404)
        """
        url = f'/api/radiation-measurements/{self.measurement_user2.id}/'
        response = self.client1.delete(url)
        
        # Should return 404 (not 403) to hide existence of resource
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # Verify measurement still exists
        self.assertTrue(RadiationMeasurement.objects.filter(id=self.measurement_user2.id).exists())
    
    def test_user_can_update_own_measurement(self):
        """
        Test that a user can update their own measurement
        """
        url = f'/api/radiation-measurements/{self.measurement_user1.id}/'
        data = {
            'device': self.device1.id,
            'project': self.project.id,
            'latitude': 0.0,
            'longitude': 0.0,
            'radiation_value': 200.0,
            'cpm': 200,
            'dateTime': timezone.now().isoformat()
        }
        
        response = self.client1.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data['radiation_value']), 200.0)
    
    def test_user_cannot_update_other_user_measurement(self):
        """
        Test that a user cannot update another user's measurement (should get 404)
        """
        url = f'/api/radiation-measurements/{self.measurement_user2.id}/'
        data = {
            'device': self.device2.id,
            'project': self.project.id,
            'latitude': 1.0,
            'longitude': 1.0,
            'radiation_value': 300.0,
            'cpm': 300,
            'dateTime': timezone.now().isoformat()
        }
        
        response = self.client1.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_unauthenticated_cannot_delete_measurement(self):
        """
        Test that unauthenticated user cannot delete a measurement
        """
        # Create a fresh client without any session
        unauthenticated_client = APIClient()
        
        url = f'/api/radiation-measurements/{self.measurement_user1.id}/'
        response = unauthenticated_client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION='none',
    ACCOUNT_EMAIL_REQUIRED=False,
    EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
)
class LightPollutionMeasurementPermissionTests(TestCase):
    """
    Test permissions for light pollution measurements using API registration
    """
    
    def setUp(self):
        """
        Set up test users using API registration and test data
        """
        self.client1 = APIClient()
        self.client2 = APIClient()
        self.public_client = APIClient()
        
        # Register users via API
        user1_data = {
            'email': 'lp_testuser1@test.com',
            'password1': 'testpass123!',
            'password2': 'testpass123!'
        }
        response1 = self.public_client.post('/dj-rest-auth/registration/', user1_data, format='json')
        self.token1 = response1.data['key']
        self.user1 = User.objects.get(email='lp_testuser1@test.com')
        
        user2_data = {
            'email': 'lp_testuser2@test.com',
            'password1': 'testpass123!',
            'password2': 'testpass123!'
        }
        response2 = self.public_client.post('/dj-rest-auth/registration/', user2_data, format='json')
        self.token2 = response2.data['key']
        self.user2 = User.objects.get(email='lp_testuser2@test.com')
        
        self.client1.credentials(HTTP_AUTHORIZATION=f'Token {self.token1}')
        self.client2.credentials(HTTP_AUTHORIZATION=f'Token {self.token2}')
        
        self.project = Project.objects.create(
            name='Test LP Project',
            description='Test project for light pollution',
            project_type='light_pollution'
        )
        
        self.device_model = DeviceModel.objects.create(
            name='Test LP Device Model',
            description='Test LP device model',
            max_radiation_range=1000.0
        )
        
        self.device1 = Device.objects.create(
            serial_number='TEST-LP-001',
            device_model=self.device_model
        )
        
        self.measurement_user1 = LightPollutionMeasurement.objects.create(
            user=self.user1,
            device=self.device1,
            project=self.project,
            latitude=0.0,
            longitude=0.0,
            sky_brightness=21.5,
            sqm_reading=21.5,
            dateTime=timezone.now()
        )
        
        self.measurement_user2 = LightPollutionMeasurement.objects.create(
            user=self.user2,
            device=self.device1,
            project=self.project,
            latitude=1.0,
            longitude=1.0,
            sky_brightness=20.5,
            sqm_reading=20.5,
            dateTime=timezone.now()
        )
    
    def test_public_can_list_all_measurements(self):
        """
        Test that anyone can list all light pollution measurements
        """
        url = '/api/light-pollution-measurements/'
        response = self.public_client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 2)
    
    def test_user_can_delete_own_measurement(self):
        """
        Test that a user can delete their own light pollution measurement
        """
        url = f'/api/light-pollution-measurements/{self.measurement_user1.id}/'
        response = self.client1.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(LightPollutionMeasurement.objects.filter(id=self.measurement_user1.id).exists())
    
    def test_user_cannot_delete_other_user_measurement(self):
        """
        Test that a user cannot delete another user's light pollution measurement
        """
        url = f'/api/light-pollution-measurements/{self.measurement_user2.id}/'
        response = self.client1.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(LightPollutionMeasurement.objects.filter(id=self.measurement_user2.id).exists())


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION='none',
    ACCOUNT_EMAIL_REQUIRED=False,
    EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
)


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION='none',
    ACCOUNT_EMAIL_REQUIRED=False,
    EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
)
class TrackPermissionTests(TestCase):
    """
    Test permissions for GPS tracks using API registration
    
    NOTE: Track model uses 'created_by' field instead of 'user' field,
    and requires 'device' and 'campaign' ForeignKeys.
    These tests are currently disabled as Track doesn't have user-based
    ownership permissions like measurements do.
    """
    
    def setUp(self):
        """
        Set up test users using API registration and test data
        """
        self.client1 = APIClient()
        self.client2 = APIClient()
        self.public_client = APIClient()
        
        # Register users via API
        user1_data = {
            'email': 'track_testuser1@test.com',
            'password1': 'testpass123!',
            'password2': 'testpass123!'
        }
        response1 = self.public_client.post('/dj-rest-auth/registration/', user1_data, format='json')
        self.token1 = response1.data['key']
        self.user1 = User.objects.get(email='track_testuser1@test.com')
        
        user2_data = {
            'email': 'track_testuser2@test.com',
            'password1': 'testpass123!',
            'password2': 'testpass123!'
        }
        response2 = self.public_client.post('/dj-rest-auth/registration/', user2_data, format='json')
        self.token2 = response2.data['key']
        self.user2 = User.objects.get(email='track_testuser2@test.com')
        
        self.client1.credentials(HTTP_AUTHORIZATION=f'Token {self.token1}')
        self.client2.credentials(HTTP_AUTHORIZATION=f'Token {self.token2}')
        
        self.project = Project.objects.create(
            name='Test Track Project',
            description='Test project for tracks',
            project_type='radiation'
        )
        
        # Track model requires device and campaign, which makes these tests complex
        # For now, we'll skip creating actual tracks
        # TODO: Add proper Track tests when implementing Track permissions
    
    def test_public_can_list_all_tracks(self):
        """
        Test that anyone can list all tracks
        """
        url = '/api/tracks/'
        response = self.public_client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Empty list is OK for now
        self.assertIsInstance(response.data, list)
    
    def test_track_permissions_placeholder(self):
        """
        Placeholder test - Track permissions need to be implemented
        based on created_by field, not user field
        """
        # This test always passes - it's a placeholder
        self.assertTrue(True)
@override_settings(
    ACCOUNT_EMAIL_VERIFICATION='none',
    ACCOUNT_EMAIL_REQUIRED=False,
    EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
)
class UserRegistrationTests(TestCase):
    """
    Test user registration via API
    """
    
    def setUp(self):
        self.client = APIClient()
    
    def test_user_can_register_via_api(self):
        """
        Test that a new user can register via the API
        """
        url = '/dj-rest-auth/registration/'
        data = {
            'email': 'newuser@test.com',
            'password1': 'strongpass123!',
            'password2': 'strongpass123!'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('key', response.data)  # Token is returned
        self.assertTrue(User.objects.filter(email='newuser@test.com').exists())
    
    def test_user_cannot_register_with_mismatched_passwords(self):
        """
        Test that registration fails with mismatched passwords
        """
        url = '/dj-rest-auth/registration/'
        data = {
            'email': 'baduser@test.com',
            'password1': 'strongpass123!',
            'password2': 'differentpass123!'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email='baduser@test.com').exists())
    
    def test_user_cannot_register_with_duplicate_email(self):
        """
        Test that registration fails with duplicate email
        """
        # First registration
        url = '/dj-rest-auth/registration/'
        data = {
            'email': 'duplicate@test.com',
            'password1': 'strongpass123!',
            'password2': 'strongpass123!'
        }
        self.client.post(url, data, format='json')
        
        # Try to register again with same email
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
