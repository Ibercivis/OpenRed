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


class Command(BaseCommand):
    help = 'Populate location fields from existing latitude/longitude values'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5000,
            help='Number of records to process per batch (default: 5000)',
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

        for model, label in [
            (RadiationMeasurement, 'RadiationMeasurement'),
            (LightPollutionMeasurement, 'LightPollutionMeasurement'),
        ]:
            self.stdout.write(f'\n{"="*60}')
            self.stdout.write(f'Processing {label}...')
            self.stdout.write('='*60)

            qs = model.objects.filter(
                latitude__isnull=False,
                longitude__isnull=False,
                location__isnull=True
            ).only('id', 'latitude', 'longitude')

            total = qs.count()
            self.stdout.write(f'Found {total} records to update')

            if dry_run or total == 0:
                if dry_run:
                    self.stdout.write(self.style.WARNING(f'Would update {total} records'))
                else:
                    self.stdout.write(self.style.SUCCESS('Nothing to update'))
                continue

            # Collect all IDs upfront to avoid queryset shifting during update
            all_ids = list(qs.values_list('id', flat=True))
            updated = 0
            for i in range(0, len(all_ids), batch_size):
                chunk_ids = all_ids[i:i + batch_size]
                batch = list(model.objects.filter(id__in=chunk_ids).only('id', 'latitude', 'longitude'))
                for m in batch:
                    m.location = Point(float(m.longitude), float(m.latitude))
                model.objects.bulk_update(batch, ['location'], batch_size=batch_size)
                updated += len(batch)
                self.stdout.write(f'  {updated}/{total} ({int(updated/total*100)}%)')

            self.stdout.write(self.style.SUCCESS(f'✓ Updated {updated} {label} records'))

        self.stdout.write(self.style.SUCCESS('\nDone. Spatial index (GIST) is now usable for bbox filters.'))
