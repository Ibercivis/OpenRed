"""
Management command to update Django Site domain for the backend API.
"""
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from django.conf import settings


class Command(BaseCommand):
    help = 'Update Django Site domain for OAuth callbacks'

    def add_arguments(self, parser):
        parser.add_argument(
            '--domain',
            type=str,
            help='Backend domain (e.g., dev.ibercivis.es:8000)',
        )

    def handle(self, *args, **options):
        domain = options.get('domain')
        
        # Get current site
        site_id = getattr(settings, 'SITE_ID', 1)
        site, created = Site.objects.get_or_create(id=site_id)
        
        if not domain:
            # Show current configuration
            self.stdout.write('\n📍 Current Site configuration:')
            self.stdout.write(f'   ID: {site.id}')
            self.stdout.write(f'   Domain: {site.domain}')
            self.stdout.write(f'   Name: {site.name}')
            self.stdout.write('\n⚠️  The Site domain should be your BACKEND domain, not frontend!')
            self.stdout.write('   Example: dev.ibercivis.es:8000 or api.open-red.es')
            self.stdout.write('\n💡 To update:')
            self.stdout.write('   python manage.py update_site --domain "dev.ibercivis.es:8000"')
            self.stdout.write('')
            return
        
        # Update site
        old_domain = site.domain
        site.domain = domain
        site.name = 'OpenRed API'
        site.save()
        
        action = 'Created' if created else 'Updated'
        self.stdout.write(
            self.style.SUCCESS(f'\n✅ {action} Site successfully!')
        )
        
        if not created:
            self.stdout.write(f'\n📝 Changes:')
            self.stdout.write(f'   Domain: {old_domain} → {domain}')
        else:
            self.stdout.write(f'\n   Domain: {domain}')
        
        self.stdout.write(f'   Name: OpenRed API')
        
        # Show redirect URI
        protocol = 'https' if not domain.startswith('localhost') and '8000' not in domain.split(':')[0] else 'https'
        self.stdout.write(f'\n🔗 Add this redirect URI in Google Cloud Console:')
        self.stdout.write(f'   {protocol}://{domain}/accounts/google/login/callback/')
        self.stdout.write('')

