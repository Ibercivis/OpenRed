import os
import requests
import time
import openrouteservice  # OpenStreetMap-based routing API
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings


class Command(BaseCommand):
    help = 'Simulate movement along roads from one city to another using OpenStreetMap and add measurement values via the API'

    def add_arguments(self, parser):
        parser.add_argument('origin', type=str, help='Starting city')
        parser.add_argument('destination', type=str, help='Destination city')
        parser.add_argument('--speed', type=float, default=50, help='Speed in km/h (default: 50 km/h)')
    
    def handle(self, *args, **options):
        # API URLs
        api_url = 'http://localhost:8000/api/measurements/'
        #ors_api_key = '5b3ce3597851110001cf6248a044da3201c64f30bd7795363d807c79'  # Set your API key
        ors_api_key = settings.OSR_API_KEY
        if not ors_api_key:
            self.stdout.write(self.style.ERROR('Missing OpenRouteService API key.'))
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
            
            # Send data to API
            data = {
                "device": 1,  # Device ID
                "user": 1,  # User ID
                "latitude": lat2,
                "longitude": lon2,
                "altitude": 200,
                "values": {"radiation": 50},
                "dateTime": timezone.now().isoformat(),
                "accuracy": 1.0,
                "unit": "Sieverts",
                "notes": f"Simulated measurement on route from {origin} to {destination}",
                "weather": {"temperature": 22, "humidity": 50}
            }

            headers = {'Content-Type': 'application/json'}
            response = requests.post(api_url, json=data, headers=headers)

            if response.status_code == 201:
                self.stdout.write(self.style.SUCCESS(f'Added measurement at ({lat2}, {lon2})'))
            else:
                self.stdout.write(self.style.ERROR(f'Failed to add measurement. Response: {response.text}'))

            # Sleep to simulate travel time (assuming constant speed)
            if i < len(route_coords) - 2:
                distance = ((route_coords[i + 1][0] - route_coords[i][0])**2 + (route_coords[i + 1][1] - route_coords[i][1])**2) ** 0.5 * 111111
                travel_time = distance / speed_mps
                time.sleep(travel_time)
        
        self.stdout.write(self.style.SUCCESS('Simulation completed successfully!'))