import random
import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from measures.models import RadiationMeasurement
from devices.models import Device
from django.contrib.auth import get_user_model

# Define constants for movement simulation
METERS_IN_LATITUDE = 1 / 111111  # Approximate conversion: 1 meter = 1 / 111111 degree latitude
METERS_IN_LONGITUDE = 1 / (111111 * 0.89)  # Adjust for longitude at Zaragoza's latitude

User = get_user_model()

class Command(BaseCommand):
    help = 'Add radiation measurement values in a random walk'

    def add_arguments(self, parser):
        # Add argument for location name (optional) or latitude/longitude directly
        parser.add_argument('location', type=str, nargs='?', help='Location name, e.g., Zaragoza')
        parser.add_argument('--latitude', type=float, help='Starting latitude')
        parser.add_argument('--longitude', type=float, help='Starting longitude')
        parser.add_argument('--iterations', type=int, default=1000, help='Number of measurements to create')
        parser.add_argument('--device-id', type=int, default=1, help='Device ID to use')
        parser.add_argument('--user-id', type=int, default=1, help='User ID to use')
        parser.add_argument('--delay', type=float, default=1.0, help='Delay between measurements in seconds')

    def handle(self, *args, **options):
        # Handle location argument or latitude/longitude directly
        location = options.get('location', None)
        latitude = options.get('latitude', None)
        longitude = options.get('longitude', None)
        num_iterations = options.get('iterations', 1000)
        device_id = options.get('device_id', 1)
        user_id = options.get('user_id', 1)
        delay = options.get('delay', 1.0)

        # Hardcoded locations
        location_coordinates = {
            'Zaragoza': (41.6488, -0.8891),
            'Madrid': (40.4168, -3.7038),
            'Barcelona': (41.3851, 2.1734),
            'Valencia': (39.4699, -0.3763),
            'Seville': (37.3886, -5.9826),
            'Bilbao': (43.2630, -2.9350),
            'Malaga': (36.7213, -4.4215),
        }

        # Determine starting coordinates
        if location and location in location_coordinates:
            current_latitude, current_longitude = location_coordinates[location]
            self.stdout.write(self.style.SUCCESS(f'Starting random walk from {location}'))
        elif latitude and longitude:
            current_latitude, current_longitude = latitude, longitude
            self.stdout.write(self.style.SUCCESS(f'Starting random walk from ({latitude}, {longitude})'))
        else:
            self.stdout.write(self.style.ERROR('You must provide a valid location or latitude and longitude.'))
            return

        # Get device and user
        try:
            device = Device.objects.get(id=device_id)
        except Device.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Device with ID {device_id} does not exist'))
            return

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User with ID {user_id} does not exist'))
            return

        self.stdout.write(self.style.SUCCESS(f'Using device: {device.name} (ID: {device.id})'))
        self.stdout.write(self.style.SUCCESS(f'Using user: {user.username} (ID: {user.id})'))
        self.stdout.write(self.style.SUCCESS(f'Creating {num_iterations} measurements...'))

        measurements_created = 0

        for i in range(num_iterations):
            # Simulate a random walk (change latitude and longitude slightly)
            current_latitude += random.uniform(-1, 1) * METERS_IN_LATITUDE * 500  # ~500 meters
            current_longitude += random.uniform(-1, 1) * METERS_IN_LONGITUDE * 500

            try:
                # Create radiation measurement directly in the database
                measurement = RadiationMeasurement.objects.create(
                    device=device,
                    user=user,
                    latitude=current_latitude,
                    longitude=current_longitude,
                    altitude=random.uniform(180, 220),  # Random altitude around 200m
                    dose_rate=random.uniform(50, 150),  # Random dose rate value
                    radiation_unit="μSv/h",  # Microsieverts per hour
                    dateTime=timezone.now(),
                    accuracy=random.uniform(0.5, 2.0),
                    notes="Random walk measurement - auto generated",
                )

                measurements_created += 1

                if (i + 1) % 10 == 0:  # Progress every 10 measurements
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Progress: {i + 1}/{num_iterations} measurements created '
                            f'(Latest: {current_latitude:.6f}, {current_longitude:.6f})'
                        )
                    )

                # Delay between measurements
                time.sleep(delay)

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'Failed to create measurement at ({current_latitude}, {current_longitude}): {str(e)}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✓ Successfully created {measurements_created}/{num_iterations} radiation measurements'
            )
        )

