"""
Management command to clean up duplicate Google OAuth apps.
"""
from django.core.management.base import BaseCommand
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = 'Remove duplicate Google OAuth social applications, keeping only one'

    def handle(self, *args, **options):
        """Remove duplicate Google OAuth apps."""
        
        # Find all Google apps
        google_apps = SocialApp.objects.filter(provider='google')
        count = google_apps.count()
        
        self.stdout.write(f'\n📊 Found {count} Google OAuth app(s) in database')
        
        if count == 0:
            self.stdout.write(
                self.style.WARNING('❌ No Google OAuth apps found.')
            )
            self.stdout.write('Run: python manage.py setup_google_oauth')
            return
        
        if count == 1:
            app = google_apps.first()
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Only one Google OAuth app found (ID: {app.id})'
                )
            )
            self.stdout.write(f'  Client ID: {app.client_id[:30]}...')
            return
        
        # Multiple apps found - keep the first, delete the rest
        self.stdout.write(
            self.style.WARNING(
                f'\n⚠️  Found {count} duplicate Google OAuth apps. Cleaning up...'
            )
        )
        
        # List all apps
        for app in google_apps:
            self.stdout.write(
                f'  - ID: {app.id}, Name: {app.name}, Client ID: {app.client_id[:30]}...'
            )
        
        # Keep the first app, delete the rest
        first_app = google_apps.first()
        duplicate_apps = google_apps.exclude(id=first_app.id)
        
        self.stdout.write(f'\n✓ Keeping app ID: {first_app.id}')
        
        deleted_count = 0
        for app in duplicate_apps:
            self.stdout.write(f'✗ Deleting app ID: {app.id}')
            app.delete()
            deleted_count += 1
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n✓ Cleaned up {deleted_count} duplicate app(s)'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                '✓ Google OAuth is now ready to use!'
            )
        )
