"""
Tests for H3 hexagon aggregation endpoint.

Run with:
    python manage.py test measures.test_h3
    python manage.py test measures.test_h3.H3AggregationTestCase
    python manage.py test measures.test_h3 -v 2
"""

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from rest_framework import status
from decimal import Decimal
import h3

from missions.models import Project, Mission, Campaign
from measures.models import RadiationMeasurement, LightPollutionMeasurement
from devices.models import Device, DeviceModel


class H3AggregationTestCase(TestCase):
    """
    Test H3 hexagon aggregation endpoint for radiation measurements.
    """
    
    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create test device
        self.device_model = DeviceModel.objects.create(
            name='Test Device Model',
            manufacturer='Test Manufacturer',
            validatedByOpenRed=True,
            max_radiation_range=1000.0
        )
        
        self.device = Device.objects.create(
            owner=self.user,
            device_model=self.device_model,
            serial_number='TEST123'
        )
        
        # Create test project hierarchy
        self.project = Project.objects.create(
            name='Test Project',
            description='Test project for H3 aggregation'
        )
        
        self.mission = Mission.objects.create(
            project=self.project,
            name='Test Mission',
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30)
        )
        
        self.campaign = Campaign.objects.create(
            mission=self.mission,
            name='Test Campaign',
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30)
        )
        
        # Create test measurements in Madrid area
        # These coordinates should fall into different H3 hexagons
        test_measurements = [
            # Center of Madrid
            {'lat': 40.4168, 'lng': -3.7038, 'value': 0.15},
            {'lat': 40.4169, 'lng': -3.7039, 'value': 0.16},
            {'lat': 40.4170, 'lng': -3.7040, 'value': 0.14},
            
            # Slightly north (different hexagon at resolution 8)
            {'lat': 40.4268, 'lng': -3.7038, 'value': 0.25},
            {'lat': 40.4269, 'lng': -3.7039, 'value': 0.26},
            
            # Far away (definitely different hexagon)
            {'lat': 40.5000, 'lng': -3.6000, 'value': 0.35},
        ]
        
        for data in test_measurements:
            RadiationMeasurement.objects.create(
                project=self.project,
                campaign=self.campaign,
                device=self.device,
                latitude=Decimal(str(data['lat'])),
                longitude=Decimal(str(data['lng'])),
                radiation_value=Decimal(str(data['value'])),
                dateTime='2024-01-01T12:00:00Z'
            )
    
    def test_h3_aggregation_basic(self):
        """Test basic H3 aggregation functionality."""
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {'resolution': 8}
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.json()
        
        # Check response structure
        self.assertIn('resolution', data)
        self.assertIn('hexagons', data)
        self.assertIn('total_hexagons', data)
        self.assertIn('total_measurements', data)
        self.assertIn('statistics', data)
        
        # Check values
        self.assertEqual(data['resolution'], 8)
        self.assertEqual(data['total_measurements'], 6)
        self.assertGreater(data['total_hexagons'], 0)
        
        # Check hexagon structure
        if data['hexagons']:
            hexagon = data['hexagons'][0]
            self.assertIn('h3_index', hexagon)
            self.assertIn('center_lat', hexagon)
            self.assertIn('center_lng', hexagon)
            self.assertIn('avg_value', hexagon)
            self.assertIn('min_value', hexagon)
            self.assertIn('max_value', hexagon)
            self.assertIn('measurement_count', hexagon)
            self.assertIn('boundary', hexagon)
            
            # Validate H3 index format
            self.assertTrue(h3.is_valid_cell(hexagon['h3_index']))
            
            # Check boundary is a polygon
            self.assertIsInstance(hexagon['boundary'], list)
            self.assertGreater(len(hexagon['boundary']), 0)
    
    def test_h3_aggregation_different_resolutions(self):
        """Test H3 aggregation with different resolutions."""
        resolutions = [5, 8, 10]
        
        for resolution in resolutions:
            response = self.client.get(
                '/api/radiation-measurements/h3-aggregation/',
                {'resolution': resolution}
            )
            
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.json()
            
            self.assertEqual(data['resolution'], resolution)
            self.assertGreater(data['total_hexagons'], 0)
    
    def test_h3_aggregation_with_filters(self):
        """Test H3 aggregation with project filter."""
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {
                'resolution': 8,
                'project': self.project.id
            }
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertEqual(data['total_measurements'], 6)
    
    def test_h3_aggregation_with_min_count(self):
        """Test H3 aggregation with min_count filter."""
        # Request only hexagons with at least 3 measurements
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {
                'resolution': 8,
                'min_count': 3
            }
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        # All returned hexagons should have at least 3 measurements
        for hexagon in data['hexagons']:
            self.assertGreaterEqual(hexagon['measurement_count'], 3)
    
    def test_h3_aggregation_statistics(self):
        """Test H3 aggregation statistics calculation."""
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {'resolution': 8}
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        stats = data['statistics']
        self.assertIsNotNone(stats)
        self.assertIn('global_avg', stats)
        self.assertIn('global_min', stats)
        self.assertIn('global_max', stats)
        
        # Check logical consistency
        self.assertLessEqual(stats['global_min'], stats['global_avg'])
        self.assertLessEqual(stats['global_avg'], stats['global_max'])
    
    def test_h3_aggregation_invalid_resolution(self):
        """Test H3 aggregation with invalid resolution."""
        # Resolution out of range
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {'resolution': 20}  # Max is 15
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Invalid format
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {'resolution': 'invalid'}
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_h3_aggregation_empty_dataset(self):
        """Test H3 aggregation with no measurements."""
        # Filter by non-existent project
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {
                'resolution': 8,
                'project': 99999
            }
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertEqual(data['hexagons'], [])
        self.assertEqual(data['total_hexagons'], 0)
        self.assertEqual(data['total_measurements'], 0)
        self.assertIsNone(data['statistics'])
    
    def test_h3_index_consistency(self):
        """Test that H3 indexes are consistent and valid."""
        response = self.client.get(
            '/api/radiation-measurements/h3-aggregation/',
            {'resolution': 8}
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        for hexagon in data['hexagons']:
            h3_index = hexagon['h3_index']
            
            # Validate H3 index
            self.assertTrue(h3.is_valid_cell(h3_index))
            
            # Check resolution matches
            self.assertEqual(h3.get_resolution(h3_index), 8)
            
            # Verify center coordinates match H3 index
            expected_lat, expected_lng = h3.cell_to_latlng(h3_index)
            self.assertAlmostEqual(hexagon['center_lat'], expected_lat, places=5)
            self.assertAlmostEqual(hexagon['center_lng'], expected_lng, places=5)


class H3LightPollutionAggregationTestCase(TestCase):
    """
    Test H3 hexagon aggregation endpoint for light pollution measurements.
    """
    
    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create test device
        self.device_model = DeviceModel.objects.create(
            name='Test Device Model',
            manufacturer='Test Manufacturer',
            validatedByOpenRed=True,
            max_radiation_range=1000.0
        )
        
        self.device = Device.objects.create(
            owner=self.user,
            device_model=self.device_model,
            serial_number='TEST456'
        )
        
        # Create test project hierarchy
        self.project = Project.objects.create(
            name='Test Project',
            description='Test project for H3 aggregation'
        )
        
        self.mission = Mission.objects.create(
            project=self.project,
            name='Test Mission',
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30)
        )
        
        self.campaign = Campaign.objects.create(
            mission=self.mission,
            name='Test Campaign',
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30)
        )
        
        # Create test light pollution measurements
        test_measurements = [
            {'lat': 40.4168, 'lng': -3.7038, 'value': 21.5},
            {'lat': 40.4169, 'lng': -3.7039, 'value': 21.6},
            {'lat': 40.4170, 'lng': -3.7040, 'value': 21.4},
            {'lat': 40.4268, 'lng': -3.7038, 'value': 20.5},
            {'lat': 40.5000, 'lng': -3.6000, 'value': 19.5},
        ]
        
        for data in test_measurements:
            LightPollutionMeasurement.objects.create(
                project=self.project,
                campaign=self.campaign,
                device=self.device,
                latitude=Decimal(str(data['lat'])),
                longitude=Decimal(str(data['lng'])),
                sky_brightness=Decimal(str(data['value'])),
                dateTime='2024-01-01T12:00:00Z'
            )
    
    def test_h3_aggregation_light_pollution(self):
        """Test H3 aggregation for light pollution measurements."""
        response = self.client.get(
            '/api/light-pollution-measurements/h3-aggregation/',
            {'resolution': 8}
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.json()
        
        # Check response structure
        self.assertEqual(data['resolution'], 8)
        self.assertEqual(data['total_measurements'], 5)
        self.assertGreater(data['total_hexagons'], 0)
        
        # Check statistics exist
        self.assertIsNotNone(data['statistics'])
        self.assertIn('global_avg', data['statistics'])
    
    def test_h3_aggregation_with_campaign_filter(self):
        """Test H3 aggregation with campaign filter."""
        response = self.client.get(
            '/api/light-pollution-measurements/h3-aggregation/',
            {
                'resolution': 8,
                'campaign': self.campaign.id
            }
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertEqual(data['total_measurements'], 5)
