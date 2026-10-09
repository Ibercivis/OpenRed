"""
Re-point light-meter devices that were auto-registered with the generic
"RadiaCode" model to their real Opple model.

upload_json used to create every unknown device as "RadiaCode", Opple light
meters included. A device qualifies when it has at least one track, every
track is a light track, and its model is still the generic one. Until the app
reported `device.model`, the only light meter it supported was the Light Master III.

Dry run by default; pass --apply to write. Idempotent.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from devices.models import Device
from measures.models import Track
from measures.views import GENERIC_DEVICE_MODEL_NAME, _light_device_model


class Command(BaseCommand):
    help = 'Fix light-meter devices registered with the generic RadiaCode model.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the changes (default: dry run).')

    def handle(self, *args, **options):
        target = _light_device_model('opple_lm3', 'light')
        candidates = []
        for device in Device.objects.filter(device_model__name=GENERIC_DEVICE_MODEL_NAME):
            types = set(Track.objects.filter(device=device).values_list('track_type', flat=True))
            if types == {'light'}:
                candidates.append(device)

        for device in candidates:
            self.stdout.write(f'{device.serial_number}: {GENERIC_DEVICE_MODEL_NAME} -> {target.name}')

        if not options['apply']:
            self.stdout.write(self.style.WARNING(f'Dry run: {len(candidates)} device(s) would change. Use --apply.'))
            return

        with transaction.atomic():
            for device in candidates:
                device.device_model = target
                device.save(update_fields=['device_model'])
        self.stdout.write(self.style.SUCCESS(f'Updated {len(candidates)} device(s).'))
