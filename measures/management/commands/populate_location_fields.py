"""
Management command to populate location fields from latitude/longitude.

This command updates existing measurements that have lat/lng but no location field.
Run this after applying the migration that adds location fields.

Usage:
    python manage.py populate_location_fields
"""
from django.core.management.base import BaseCommand
from django.contrib.gis.geos import Point
from measures.models import RadiationMeasurement, LightPollutionMeasurement
from django.db import transaction


class Command(BaseCommand):
    help = 'Populate location fields from existing latitude/longitude values'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to process per batch (default: 1000)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        # Process RadiationMeasurement
        self.stdout.write('\n' + '='*60)
        self.stdout.write('Processing RadiationMeasurement records...')
        self.stdout.write('='*60)
        
        radiation_qs = RadiationMeasurement.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            location__isnull=True
        )
        
        total_radiation = radiation_qs.count()
        self.stdout.write(f'Found {total_radiation} records to update')
        
        if not dry_run and total_radiation > 0:
            updated_radiation = 0
            for i in range(0, total_radiation, batch_size):
                batch = radiation_qs[i:i + batch_size]
                with transaction.atomic():
                    for measurement in batch:
                        measurement.location = Point(
                            float(measurement.longitude),
                            float(measurement.latitude)
                        )
                        measurement.save(update_fields=['location'])
                        updated_radiation += 1
                
                self.stdout.write(
                    f'  Processed {min(i + batch_size, total_radiation)}/{total_radiation} '
                    f'({int((updated_radiation/total_radiation)*100)}%)'
                )
            
            self.stdout.write(
                self.style.SUCCESS(f'✓ Updated {updated_radiation} RadiationMeasurement records')
            )
        elif dry_run:
            self.stdout.write(
                self.style.WARNING(f'Would update {total_radiation} RadiationMeasurement records')
            )
        else:
            self.stdout.write(self.style.SUCCESS('No RadiationMeasurement records to update'))

        # Process LightPollutionMeasurement
        self.stdout.write('\n' + '='*60)
        self.stdout.write('Processing LightPollutionMeasurement records...')
        self.stdout.write('='*60)
        
        light_qs = LightPollutionMeasurement.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            location__isnull=True
        )
        
        total_light = light_qs.count()
        self.stdout.write(f'Found {total_light} records to update')
        
        if not dry_run and total_light > 0:
            updated_light = 0
            for i in range(0, total_light, batch_size):
                batch = light_qs[i:i + batch_size]
                with transaction.atomic():
                    for measurement in batch:
                        measurement.location = Point(
                            float(measurement.longitude),
                            float(measurement.latitude)
                        )
                        measurement.save(update_fields=['location'])
                        updated_light += 1
                
                self.stdout.write(
                    f'  Processed {min(i + batch_size, total_light)}/{total_light} '
                    f'({int((updated_light/total_light)*100)}%)'
                )
            
            self.stdout.write(
                self.style.SUCCESS(f'✓ Updated {updated_light} LightPollutionMeasurement records')
            )
        elif dry_run:
            self.stdout.write(
                self.style.WARNING(f'Would update {total_light} LightPollutionMeasurement records')
            )
        else:
            self.stdout.write(self.style.SUCCESS('No LightPollutionMeasurement records to update'))

        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write('SUMMARY')
        self.stdout.write('='*60)
        
        total_records = total_radiation + total_light
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'Would update {total_records} total records')
            )
            self.stdout.write('\nRun without --dry-run to apply changes')
        else:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Successfully updated {total_records} total records')
            )
            self.stdout.write('\nLocation fields are now populated and spatial indexes can be used!')
