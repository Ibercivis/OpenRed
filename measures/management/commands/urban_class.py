"""
Degree of Urbanisation (GHS-SMOD) support for light-pollution measurements.

Usage:
    python manage.py urban_class fetch
        Download the GHS-SMOD 1 km raster (~30 MB zip) to settings.GHS_SMOD_PATH.

    python manage.py urban_class assign [--limit N] [--all]
        Classify pending measurements (urban_class IS NULL). Same function the
        scheduler runs every 10 minutes; --all loops until nothing is pending.

    python manage.py urban_class status
        Raster availability and counts per class.
"""
import io
import os
import zipfile

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from measures.urban_class import (
    CODE_LABELS_ES,
    GHS_SMOD_FILENAME,
    GHS_SMOD_URL,
    assign_pending_urban_class,
    raster_available,
    raster_path,
)


class Command(BaseCommand):
    help = 'Descarga del ráster GHS-SMOD y clasificación ciudad/pueblo/rural de las medidas lumínicas'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('fetch')
        sub.add_parser('status')
        p_assign = sub.add_parser('assign')
        p_assign.add_argument('--limit', type=int, default=20000)
        p_assign.add_argument('--all', action='store_true', help='Repetir hasta que no quede nada pendiente')

    def handle(self, *args, **options):
        return getattr(self, f"handle_{options['action']}")(options)

    def handle_fetch(self, options):
        path = raster_path()
        if os.path.exists(path):
            self.stdout.write(f'Ya existe {path}')
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.stdout.write(f'Descargando {GHS_SMOD_URL} ...')
        response = requests.get(GHS_SMOD_URL, timeout=600)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            members = [m for m in zf.namelist() if m.endswith(('.tif', '.tif.ovr', '.clr'))]
            if GHS_SMOD_FILENAME not in members:
                raise CommandError(f'{GHS_SMOD_FILENAME} no está en el zip: {zf.namelist()}')
            for member in members:
                zf.extract(member, os.path.dirname(path))
        self.stdout.write(self.style.SUCCESS(f'Ráster guardado en {path}'))

    def handle_status(self, options):
        from measures.models import LightPollutionMeasurement
        self.stdout.write(f'Ráster: {raster_path()} ({"disponible" if raster_available() else "NO ENCONTRADO"})')
        rows = (LightPollutionMeasurement.objects.values('urban_class')
                .annotate(n=Count('id')).order_by('urban_class'))
        total = 0
        for row in rows:
            code = row['urban_class']
            label = 'pendiente' if code is None else CODE_LABELS_ES.get(code, '?')
            self.stdout.write(f'  {str(code):>5}  {label:<28} {row["n"]:>9,}')
            total += row['n']
        self.stdout.write(f'  total {total:,}')

    def handle_assign(self, options):
        if not raster_available():
            raise CommandError(f'Ráster no encontrado en {raster_path()}; ejecuta "urban_class fetch"')
        total = {'processed': 0, 'classified': 0}
        while True:
            result = assign_pending_urban_class(limit=options['limit'])
            total['processed'] += result['processed']
            total['classified'] += result['classified']
            self.stdout.write(f'  lote: {result}')
            if not options['all'] or result['processed'] == 0 or result['classified'] == 0:
                break
        self.stdout.write(self.style.SUCCESS(f'Procesadas {total["processed"]:,} · clasificadas {total["classified"]:,}'))
