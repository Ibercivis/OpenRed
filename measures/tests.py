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
from datetime import datetime, timezone as dt_timezone
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
            lux=21.5,
            dateTime=timezone.now()
        )
        
        self.measurement_user2 = LightPollutionMeasurement.objects.create(
            user=self.user2,
            device=self.device1,
            project=self.project,
            latitude=1.0,
            longitude=1.0,
            lux=20.5,
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


class RctrkParserTests(TestCase):
    """
    Tests for the RCTRK (RadiaCode export) parser.

    The RadiaCode app exports a different file depending on the platform:
    Android writes tab-separated text, iOS writes a JSON document. Both carry
    the same units (DoseRate in μR/h, CountRate in CPS) and must produce the
    same measurements.

    Run with:
        python manage.py test measures.tests.RctrkParserTests
    """

    ANDROID_CONTENT = (
        "Track: 2025-11-16 08-13-45\tRC-102-008530\tSetas\tEC\n"
        "Timestamp\tTime\tLatitude\tLongitude\tAccuracy\tDoseRate\tCountRate\tComment\n"
        "134077508536700000\t2025-11-16 07:14:13\t41.8378825\t-1.7921044\t3.68\t7.76\t6.61\t \n"
        "134077508548580000\t2025-11-16 07:14:14\t41.8378849\t-1.7921036\t4.14\t7.77\t6.62\t \n"
        "134077524440910000\t2025-11-16 07:40:44\t41.83495\t-1.7631616\t500\t6.92\t6.81\t \n"
    )

    IOS_CONTENT = """{
      "sv" : false,
      "title" : "Track 16 Nov 2025 08:33:42",
      "devices" : ["RC-102-008406"],
      "start" : 1763278422,
      "periods" : [{"distance" : 6004.12, "start" : 1763278422, "end" : 1763287996}],
      "markers" : [
        {"doseRate" : 5.73, "date" : 1763283709, "acc" : 5, "lon" : -1.822395, "lat" : 41.816321, "countRate" : 5.38},
        {"doseRate" : 8.96, "date" : 1763284204, "acc" : 8, "lon" : -1.820435, "lat" : 41.817606, "countRate" : 7.48},
        {"doseRate" : 8.36, "date" : 1763282989, "lon" : -1.823629, "lat" : 41.816547, "countRate" : 6.32},
        {"doseRate" : "bad", "date" : 1763282990, "lon" : -1.82, "lat" : 41.81, "countRate" : 6.0}
      ]
    }"""

    def setUp(self):
        self.user = User.objects.create_user(username='rctrk_user', email='rctrk@test.com', password='x')
        self.project = Project.objects.create(name='RCTRK project', description='', project_type='radiation')
        self.device_model = DeviceModel.objects.create(name='Radiacode 102', manufacturer='Radiacode', description='', max_radiation_range=1000.0)
        self.device = Device.objects.create(serial_number='RC-102-TEST', device_model=self.device_model)

    def _make_track(self):
        return Track.objects.create(
            created_by=self.user, project=self.project, device=self.device,
            file_type='rctrk', status='pending',
        )

    def test_detect_format(self):
        from .tasks import detect_rctrk_format
        self.assertEqual(detect_rctrk_format(self.ANDROID_CONTENT), 'android')
        self.assertEqual(detect_rctrk_format(self.ANDROID_CONTENT.encode()), 'android')
        self.assertEqual(detect_rctrk_format(self.IOS_CONTENT), 'ios')
        self.assertEqual(detect_rctrk_format(('﻿' + self.IOS_CONTENT).encode('utf-8')), 'ios')

    def test_android_format_creates_measurements(self):
        from .tasks import parse_rctrk_track
        track = self._make_track()
        count = parse_rctrk_track(track, self.ANDROID_CONTENT.encode('utf-8'))
        self.assertEqual(count, 3)
        m = RadiationMeasurement.objects.filter(track=track).order_by('dateTime').first()
        self.assertAlmostEqual(float(m.dose_rate), 0.0776, places=6)
        self.assertEqual(m.cpm, 396)
        self.assertAlmostEqual(m.accuracy, 3.68)
        self.assertEqual(m.dateTime, datetime(2025, 11, 16, 7, 14, 13, tzinfo=dt_timezone.utc))
        track.refresh_from_db()
        self.assertEqual(track.start_time, m.dateTime)
        self.assertIsNotNone(track.avg_dose_rate)

    def test_ios_format_creates_measurements(self):
        from .tasks import parse_rctrk_track
        track = self._make_track()
        count = parse_rctrk_track(track, self.IOS_CONTENT.encode('utf-8'))
        # 3 valid markers; the one with a non-numeric doseRate is skipped
        self.assertEqual(count, 3)
        ms = list(RadiationMeasurement.objects.filter(track=track).order_by('dateTime'))
        # Earliest marker is the third one (date 1763282989 = 2025-11-16 08:49:49 UTC)
        self.assertEqual(ms[0].dateTime, datetime(2025, 11, 16, 8, 49, 49, tzinfo=dt_timezone.utc))
        self.assertAlmostEqual(float(ms[0].dose_rate), 0.0836, places=6)
        self.assertEqual(ms[0].cpm, 379)  # int(6.32 * 60)
        self.assertIsNone(ms[0].accuracy)  # marker without "acc"
        self.assertAlmostEqual(ms[1].accuracy, 5.0)
        self.assertEqual(ms[1].cpm, 322)  # int(5.38 * 60)
        for m in ms:
            self.assertEqual(m.user, self.user)
            self.assertEqual(m.project, self.project)
            self.assertEqual(m.device, self.device)
            self.assertEqual(m.radiation_unit, 'μSv/h')
        track.refresh_from_db()
        self.assertEqual(track.start_time, ms[0].dateTime)
        self.assertEqual(track.end_time, ms[-1].dateTime)
        self.assertAlmostEqual(track.min_dose_rate, 0.0573, places=6)
        self.assertAlmostEqual(track.max_dose_rate, 0.0896, places=6)

    def test_ios_and_android_give_same_values_for_same_point(self):
        from .tasks import _parse_rctrk_android_points, _parse_rctrk_ios_points
        android = _parse_rctrk_android_points(
            "Track: x\ty\tz\tEC\nTimestamp\tTime\tLatitude\tLongitude\tAccuracy\tDoseRate\tCountRate\tComment\n"
            "1\t2025-11-16 09:01:49\t41.816321\t-1.822395\t5\t5.73\t5.38\t \n"
        )
        ios = _parse_rctrk_ios_points(
            '{"markers": [{"doseRate": 5.73, "date": 1763283709, "acc": 5, "lon": -1.822395, "lat": 41.816321, "countRate": 5.38}]}'
        )
        self.assertEqual(len(android), 1)
        self.assertEqual(len(ios), 1)
        self.assertEqual(android[0]['dateTime'], ios[0]['dateTime'])
        for key in ('latitude', 'longitude', 'accuracy', 'dose_rate', 'cpm'):
            self.assertEqual(android[0][key], ios[0][key], key)

    def test_android_2026_layouts_map_columns_by_name(self):
        from .tasks import _parse_rctrk_android_points
        # Altitude, Accuracy, Temperature inserted before DoseRate (April 2026 export)
        pts = _parse_rctrk_android_points(
            "Track: 2026-04-25 10-33-39\tRC-102\tLlanes\tEC\n"
            "Timestamp\tTime\tLatitude\tLongitude\tAltitude\tAccuracy\tTemperature\tDoseRate\tCountRate\tComment\n"
            "134215796252610000\t2026-04-25 08:33:45\t43.4238722\t-4.7556933\t79.8\t4.19\t31.9\t3.68\t2.61\t \n"
        )
        self.assertEqual(len(pts), 1)
        self.assertAlmostEqual(pts[0]['accuracy'], 4.19)
        self.assertAlmostEqual(pts[0]['altitude'], 79.8)
        self.assertAlmostEqual(pts[0]['dose_rate'], 0.0368)
        self.assertEqual(pts[0]['cpm'], 156)  # int(2.61 * 60)
        # Altitude in place of Accuracy, Accuracy/Temperature appended after Comment (May 2026 export)
        pts = _parse_rctrk_android_points(
            "Track: x\tRC-102\t\tEC\n"
            "Timestamp\tTime\tLatitude\tLongitude\tAltitude\tDoseRate\tCountRate\tComment\tAccuracy\tTemperature\n"
            "1.3421573213518E+017\t2026-04-25 06:46:53\t43.3192735\t-3.0215848\t96.3\t5.71\t4.08\t \t11.7\t29.4\n"
            "1.3421573213519E+017\t2026-04-25 06:46:54\t43.3192736\t-3.0215849\t96.3\t5.70\t4.00\t \n"
        )
        self.assertEqual(len(pts), 2)
        self.assertAlmostEqual(pts[0]['accuracy'], 11.7)
        self.assertAlmostEqual(pts[0]['altitude'], 96.3)
        self.assertAlmostEqual(pts[0]['dose_rate'], 0.0571)
        self.assertIsNone(pts[1]['accuracy'])  # short row: trailing columns missing
        # Legacy 2025 layout still yields no altitude
        pts = _parse_rctrk_android_points(self.ANDROID_CONTENT)
        self.assertEqual(len(pts), 3)
        self.assertIsNone(pts[0]['altitude'])

    def test_device_serial_from_file(self):
        from .views import _rctrk_device_serial
        self.assertEqual(_rctrk_device_serial(self.ANDROID_CONTENT), 'RC-102-008530')
        self.assertEqual(_rctrk_device_serial(self.IOS_CONTENT.encode('utf-8')), 'RC-102-008406')
        self.assertEqual(_rctrk_device_serial('Track: x\trc-103g-000245\t\tEC\nh\n'), 'RC-103G-000245')
        self.assertIsNone(_rctrk_device_serial('Track: x\tRC-102\t\tEC\nh\n'))       # model only
        self.assertIsNone(_rctrk_device_serial('Track: x\t \t \tEC\nh\n'))            # blank
        self.assertIsNone(_rctrk_device_serial('{"markers": []}'))                    # no devices key
        self.assertIsNone(_rctrk_device_serial('{not json'))

    def test_device_resolution_without_device(self):
        from .views import _device_for_rctrk_upload, RCTRK_UNKNOWN_DEVICE_SERIAL
        # Serial in file -> device auto-created for the user, model from the prefix
        d = _device_for_rctrk_upload(self.user, self.IOS_CONTENT)
        self.assertEqual(d.serial_number, 'RC-102-008406')
        self.assertEqual(d.owner, self.user)
        self.assertEqual(d.device_model.name, 'Radiacode 102')  # picked from the RC-102 prefix
        # Second time -> same device, no duplicate
        self.assertEqual(_device_for_rctrk_upload(self.user, self.IOS_CONTENT).id, d.id)
        # No serial -> shared placeholder, no owner
        u = _device_for_rctrk_upload(self.user, 'Track: x\tRC-102\t\tEC\nh\n')
        self.assertEqual(u.serial_number, RCTRK_UNKNOWN_DEVICE_SERIAL)
        self.assertIsNone(u.owner)
        self.assertEqual(_device_for_rctrk_upload(self.user, 'Track: y\t\t\tEC\nh\n').id, u.id)

    def test_ios_without_markers_raises(self):
        from .tasks import parse_rctrk_track
        track = self._make_track()
        with self.assertRaises(ValueError):
            parse_rctrk_track(track, '{"title": "empty"}')
        with self.assertRaises(ValueError):
            parse_rctrk_track(track, '{"markers": []}')
        with self.assertRaises(ValueError):
            parse_rctrk_track(track, '{not json')
