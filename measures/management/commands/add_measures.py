import os
import requests
import random
import time
from django.core.management.base import BaseCommand
from django.utils import timezone

# Define constants for movement simulation
METERS_IN_LATITUDE = 1 / 111111  # Approximate conversion: 1 meter = 1 / 111111 degree latitude
METERS_IN_LONGITUDE = 1 / (111111 * 0.89)  # Adjust for longitude at Zaragoza's latitude

class Command(BaseCommand):
    help = 'Add measurement values via the API in a random walk'

    def add_arguments(self, parser):
        # Add argument for location name (optional) or latitude/longitude directly
        parser.add_argument('location', type=str, nargs='?', help='Location name, e.g., Zaragoza')
        parser.add_argument('--latitude', type=float, help='Starting latitude')
        parser.add_argument('--longitude', type=float, help='Starting longitude')

    def handle(self, *args, **options):
        # URL of your API endpoint
        api_url = 'http://localhost:8000/api/measurements/'

        # Handle location argument or latitude/longitude directly
        location = options.get('location', None)
        latitude = options.get('latitude', None)
        longitude = options.get('longitude', None)

        # Hardcoded locations (Zaragoza example)
        location_coordinates = {
            'Zaragoza': (41.6488, -0.8891),  # Latitude, Longitude of Zaragoza
            'Madrid': (40.4168, -3.7038),  # Latitude, Longitude of Madrid
            'Barcelona': (41.3851, 2.1734),  # Latitude, Longitude of Barcelona
            'Valencia': (39.4699, -0.3763),  # Latitude, Longitude of Valencia
            'Seville': (37.3886, -5.9826),  # Latitude, Longitude of Seville
            'Bilbao': (43.2630, -2.9350),  # Latitude, Longitude of Bilbao
            'Malaga': (36.7213, -4.4215),  # Latitude, Longitude of Malaga
            # Add more locations if needed
        }

        # Determine starting coordinates based on location or provided lat/long
        if location and location in location_coordinates:
            current_latitude, current_longitude = location_coordinates[location]
        elif latitude and longitude:
            current_latitude, current_longitude = latitude, longitude
        else:
            self.stdout.write(self.style.ERROR('You must provide a valid location or latitude and longitude.'))
            return

        # Number of iterations (optional): Stop after 10 measurements
        num_iterations = 1000

        for _ in range(num_iterations):
            # Simulate a random walk (change latitude and longitude slightly)
            current_latitude += random.uniform(-1, 1) * METERS_IN_LATITUDE * 500  # Adjust for 50 meters
            current_longitude += random.uniform(-1, 1) * METERS_IN_LONGITUDE * 500  # Adjust for 50 meters


            # Example data to be sent
            data = {
                "device": 1,  # Device ID
                "user": 1,  # User ID
                "latitude": current_latitude,
                "longitude": current_longitude,
                "altitude": 200,
                "values": {"radiation": random.uniform(50, 100)},  # Random radiation value
                "dateTime": timezone.now().isoformat(),  # Current time in ISO format
                "accuracy": 1.0,
                "unit": "Sieverts",
                "notes": "Random walk measurement added via API",
                "weather": {"temperature": 22, "humidity": 50}
            }

            # Optional: Authentication if needed (Bearer token example)
            headers = {
                'X-CSRFToken': 'rVFqCl8tria1mSouQnZiRlxx8URnw5ePfq1f2wFuLJEPbKwDY9O7gYEUZqVFqkH2',
                'Content-Type': 'application/json',
            }

            # Make the POST request to the API
            response = requests.post(api_url, json=data, headers=headers)

            if response.status_code == 201:
                self.stdout.write(self.style.SUCCESS(f'Successfully added measurement at ({current_latitude}, {current_longitude})'))
            else:
                self.stdout.write(self.style.ERROR(f'Failed to add measurement. Status code: {response.status_code}'))
                self.stdout.write(self.style.ERROR(f'Response: {response.text}'))

            # Wait for 10 seconds before adding the next measurement
            time.sleep(1)

