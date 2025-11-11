"""
Management command to update Django Site with FRONTEND_URL from settings.
"""
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from django.conf import settings
from urllib.parse import urlparse


class Command(BaseCommand):
    help = 'Update Django Site domain and name from FRONTEND_URL setting'

    def handle(self, *args, **options):
        # Get FRONTEND_URL from settings
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        parsed = urlparse(frontend_url)
        domain = parsed.netloc  # e.g., 192.168.1.3:3000 or map.open-red.es
        
        # Get or create Site with SITE_ID
        site_id = getattr(settings, 'SITE_ID', 1)
        site, created = Site.objects.get_or_create(id=site_id)
        
        # Update domain and name
        site.domain = domain
        site.name = 'OpenRed'
        site.save()
        
        if created:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Created Site: {site.name} ({site.domain})')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Updated Site: {site.name} ({site.domain})')
            )
        
        self.stdout.write(
            self.style.SUCCESS(f'\n  FRONTEND_URL: {frontend_url}')
        )
        self.stdout.write(
            self.style.SUCCESS(f'  Site domain: {site.domain}')
        )
        self.stdout.write(
            self.style.SUCCESS(f'  Site name: {site.name}\n')
        )
