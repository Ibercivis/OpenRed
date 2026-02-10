import os
import requests
import time
import openrouteservice  # OpenStreetMap-based routing API
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
import random   # For generating random radiation values


class Command(BaseCommand):
    help = 'Simulate movement along roads from one city to another using OpenStreetMap and add measurement values via the API'

    def add_arguments(self, parser):
        parser.add_argument('origin', type=str, help='Starting city')
        parser.add_argument('destination', type=str, help='Destination city')
        parser.add_argument('--speed', type=float, default=50, help='Speed in km/h (default: 50 km/h)')
        parser.add_argument('--project', type=int, default=1, help='Project ID to assign measurements to (default: 1)')
        parser.add_argument('--device', type=int, default=3, help='Device ID to use for measurements (default: 3)')
        parser.add_argument('--user', type=int, default=2, help='User ID to assign measurements to (default: 2)')
        parser.add_argument('--token', type=str, help='Authentication token (Bearer token)')
    
    def handle(self, *args, **options):
        from missions.models import Project  # Import here to avoid circular imports
        from devices.models import Device
        from django.contrib.auth.models import User
        
        # Get OpenRouteService API key
        #ors_api_key = '5b3ce3597851110001cf6248a044da3201c64f30bd7795363d807c79'  # Set your API key
        ors_api_key = settings.OSR_API_KEY
        if not ors_api_key:
            self.stdout.write(self.style.ERROR('Missing OpenRouteService API key.'))
            return

        # Validate project exists and determine measurement type
        project_id = options['project']
        device_id = options['device']
        user_id = options['user']
        auth_token = options.get('token')
        
        try:
            project = Project.objects.get(id=project_id)
            self.stdout.write(self.style.SUCCESS(f'Using project: {project.name} ({project.project_type})'))
            
            # Determine API endpoint based on project type
            # Use localhost for local testing, or get from environment
            base_url = os.environ.get('API_BASE_URL', 'http://localhost:8000')
            
            if project.project_type == 'radiation':
                api_url = f'{base_url}/api/radiation-measurements/'
            elif project.project_type == 'light_pollution':
                api_url = f'{base_url}/api/light-pollution-measurements/'
            else:
                self.stdout.write(self.style.ERROR(f'Unsupported project type: {project.project_type}'))
                return
                
        except Project.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Project with ID {project_id} does not exist.'))
            return

        # Validate device exists
        try:
            device = Device.objects.get(id=device_id)
            self.stdout.write(self.style.SUCCESS(f'Using device: {device.serial_number} (ID: {device.id})'))
        except Device.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Device with ID {device_id} does not exist.'))
            return

        # Validate user exists
        try:
            user = User.objects.get(id=user_id)
            self.stdout.write(self.style.SUCCESS(f'Using user: {user.username} (ID: {user.id})'))
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User with ID {user_id} does not exist.'))
            return

        # OpenRouteService client
        client = openrouteservice.Client(key=ors_api_key)

        # Validate inputs
        origin = options['origin']
        destination = options['destination']
        speed_kmh = options['speed']

        # Get coordinates of the origin and destination
        try:
            geocode_origin = client.pelias_search(origin)
            geocode_destination = client.pelias_search(destination)
        except openrouteservice.exceptions._OverQueryLimit:
            self.stdout.write(self.style.ERROR('Rate limit exceeded while geocoding. Try again later.'))
            return

        if not geocode_origin or not geocode_destination:
            self.stdout.write(self.style.ERROR('Could not geocode origin or destination.'))
            return

        origin_coords = geocode_origin['features'][0]['geometry']['coordinates']
        destination_coords = geocode_destination['features'][0]['geometry']['coordinates']

        # Get route (only once!)
        try:
            route = client.directions(
                coordinates=[origin_coords, destination_coords],
                profile='driving-car',
                format='geojson'
            )
        except openrouteservice.exceptions._OverQueryLimit:
            self.stdout.write(self.style.ERROR('Rate limit exceeded while fetching route. Try again later.'))
            return

        if not route or 'features' not in route or not route['features']:
            self.stdout.write(self.style.ERROR('Failed to get a route from OpenRouteService.'))
            return

        # Extract coordinates from the route (use once, avoid API calls in loop)
        route_coords = route['features'][0]['geometry']['coordinates']
        speed_mps = (speed_kmh * 1000) / 3600  # Convert km/h to meters per second

        self.stdout.write(self.style.SUCCESS(f'Starting movement from {origin} to {destination} at {speed_kmh} km/h.'))

        

        for i in range(len(route_coords) - 1):
            lon2, lat2 = route_coords[i + 1]

            # Generate measurement data based on project type
            if project.project_type == 'radiation':
                # Generate random radiation value between 50 and 200 nSv/h (more realistic range)
                measurement_value = round(random.uniform(50, 200), 2)
                
                data = {
                    "device": device_id,  # Device ID (required)
                    "user": user_id,  # User ID (required) 
                    "project": project_id,  # Project ID (required)
                    "latitude": lat2,
                    "longitude": lon2,
                    "altitude": round(random.uniform(100, 300), 1),  # Random altitude
                    "dose_rate": measurement_value,  # Main dose rate field
                    "radiation_unit": "nSv/h",  # Unit for radiation
                    "dateTime": timezone.now().isoformat(),
                    "accuracy": round(random.uniform(1.0, 5.0), 1),  # GPS accuracy
                    "notes": f"Simulated gamma radiation measurement on route from {origin} to {destination}",
                }
                measurement_unit = "nSv/h"
                
            elif project.project_type == 'light_pollution':
                # Generate random lux value (illustrative)
                measurement_value = round(random.uniform(0.0, 500.0), 2)
                
                data = {
                    "device": device_id,  # Device ID (required)
                    "user": user_id,  # User ID (required)
                    "project": project_id,  # Project ID (required)
                    "latitude": lat2,
                    "longitude": lon2,
                    "altitude": round(random.uniform(100, 300), 1),  # Random altitude
                    "lux": measurement_value,
                    "dateTime": timezone.now().isoformat(),
                    "accuracy": round(random.uniform(1.0, 5.0), 1),  # GPS accuracy
                    "notes": f"Simulated lux light pollution measurement on route from {origin} to {destination}",
                }
                measurement_unit = "lux"

            # Prepare headers with authentication
            headers = {'Content-Type': 'application/json'}
            if auth_token:
                # Clean token if it includes "Token:" prefix
                clean_token = auth_token.replace('Token:', '').strip()
                headers['Authorization'] = f'Token {clean_token}'
            
            response = requests.post(api_url, json=data, headers=headers)

            if response.status_code == 201:
                self.stdout.write(self.style.SUCCESS(f'Added measurement at ({lat2:.4f}, {lon2:.4f}) - {measurement_value} {measurement_unit} to project {project.name}'))
            else:
                self.stdout.write(self.style.ERROR(f'Failed to add measurement. Response: {response.text}'))

            # Optional: Small delay to avoid overwhelming the API (0.1 seconds)
            time.sleep(0.1)
        
        self.stdout.write(self.style.SUCCESS(f'Simulation completed successfully! Added {len(route_coords)-1} measurements to project "{project.name}"'))