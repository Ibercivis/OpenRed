"""
Management command to setup Google OAuth provider for django-allauth.

Usage:
    python manage.py setup_google_oauth

This command will:
- Create or update the Google social application in the database
- Configure it with credentials from environment variables
- Associate it with the current site
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site


class Command(BaseCommand):
    help = 'Setup Google OAuth provider for social authentication'

    def handle(self, *args, **options):
        """Configure Google OAuth provider."""
        
        # Get Google credentials from environment variables
        client_id = settings.SOCIALACCOUNT_PROVIDERS.get('google', {}).get('CLIENT_ID', '')
        client_secret = settings.SOCIALACCOUNT_PROVIDERS.get('google', {}).get('SECRET', '')
        
        # Validate credentials
        if not client_id or not client_secret:
            self.stdout.write(
                self.style.ERROR(
                    '❌ Error: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in your .env file'
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    '\nPlease add the following to your .env file:'
                )
            )
            self.stdout.write('GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com')
            self.stdout.write('GOOGLE_CLIENT_SECRET=your-client-secret')
            self.stdout.write(
                self.style.WARNING(
                    '\nGet your credentials from: https://console.cloud.google.com/apis/credentials'
                )
            )
            return
        
        # Get current site
        try:
            site = Site.objects.get_current()
            self.stdout.write(f'📍 Using site: {site.domain} (ID: {site.id})')
        except Site.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(
                    '❌ Error: No site found. Run "python manage.py migrate" first.'
                )
            )
            return
        
        # Check for existing Google apps
        existing_apps = SocialApp.objects.filter(provider='google')
        
        if existing_apps.count() > 1:
            self.stdout.write(
                self.style.ERROR(
                    f'❌ Error: Found {existing_apps.count()} Google OAuth apps in database!'
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    'Run "python manage.py cleanup_google_oauth" to fix this.'
                )
            )
            return
        
        # Create or update Google social app
        social_app, created = SocialApp.objects.get_or_create(
            provider='google',
            defaults={
                'name': 'Google',
                'client_id': client_id,
                'secret': client_secret,
            }
        )
        
        if not created:
            # Update existing credentials
            social_app.client_id = client_id
            social_app.secret = client_secret
            social_app.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Updated existing Google OAuth configuration'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Created new Google OAuth configuration'
                )
            )
        
        # Associate with site
        if site not in social_app.sites.all():
            social_app.sites.add(site)
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Associated Google OAuth with site: {site.domain}'
                )
            )
        
        # Display configuration info
        self.stdout.write(
            self.style.SUCCESS(
                '\n🎉 Google OAuth is now configured!'
            )
        )
        self.stdout.write('\n📝 Configuration details:')
        self.stdout.write(f'   Provider: {social_app.provider}')
        self.stdout.write(f'   Client ID: {social_app.client_id[:20]}...')
        self.stdout.write(f'   Sites: {", ".join([s.domain for s in social_app.sites.all()])}')
        
        self.stdout.write('\n🔧 Next steps:')
        self.stdout.write('   1. Configure OAuth consent screen in Google Cloud Console')
        self.stdout.write('   2. Add authorized redirect URIs:')
        
        # Show redirect URIs for each site
        for site in social_app.sites.all():
            protocol = 'https' if not settings.DEBUG else 'http'
            redirect_uri = f'{protocol}://{site.domain}/accounts/google/login/callback/'
            self.stdout.write(f'      - {redirect_uri}')
        
        self.stdout.write('\n📚 API Endpoints:')
        self.stdout.write('   - Login with Google: POST /dj-rest-auth/google/')
        self.stdout.write('     Body: {"access_token": "google_access_token"}')
        self.stdout.write('\n   - Web flow: GET /accounts/google/login/')
        
        self.stdout.write(
            self.style.SUCCESS(
                '\n✓ Setup complete! You can now use Google authentication.'
            )
        )
