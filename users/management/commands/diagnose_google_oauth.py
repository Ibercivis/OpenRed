"""
Diagnóstico de Google OAuth - verifica qué está fallando
"""
import sys
import requests
from django.core.management.base import BaseCommand
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = 'Diagnose Google OAuth configuration issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--token',
            type=str,
            help='Google access_token to test',
        )

    def handle(self, *args, **options):
        """Diagnose Google OAuth setup."""
        
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write('  GOOGLE OAUTH DIAGNOSTICS')
        self.stdout.write('=' * 60 + '\n')
        
        # Check 1: Social App in database
        self.stdout.write(self.style.HTTP_INFO('\n1️⃣  Checking SocialApp in database...'))
        try:
            app = SocialApp.objects.get(provider='google')
            self.stdout.write(self.style.SUCCESS(f'   ✓ Found Google OAuth app (ID: {app.id})'))
            self.stdout.write(f'     Client ID: {app.client_id[:30]}...')
            self.stdout.write(f'     Sites: {", ".join([s.domain for s in app.sites.all()])}')
        except SocialApp.DoesNotExist:
            self.stdout.write(self.style.ERROR('   ✗ No Google OAuth app found!'))
            self.stdout.write('     Run: python manage.py setup_google_oauth')
            return
        except SocialApp.MultipleObjectsReturned:
            self.stdout.write(self.style.ERROR('   ✗ Multiple Google OAuth apps found!'))
            self.stdout.write('     Run: python manage.py cleanup_google_oauth')
            return
        
        # Check 2: Test if token is provided
        token = options.get('token')
        if not token:
            self.stdout.write(self.style.WARNING('\n2️⃣  No --token provided, skipping API tests'))
            self.stdout.write('     To test a token, run:')
            self.stdout.write('     python manage.py diagnose_google_oauth --token "YOUR_ACCESS_TOKEN"')
        else:
            self.stdout.write(self.style.HTTP_INFO('\n2️⃣  Testing Google access_token...'))
            self._test_token(token)
        
        # Check 3: Environment variables
        self.stdout.write(self.style.HTTP_INFO('\n3️⃣  Checking environment variables...'))
        from django.conf import settings
        providers = settings.SOCIALACCOUNT_PROVIDERS.get('google', {})
        self.stdout.write(f'     SCOPE: {providers.get("SCOPE", [])}')
        self.stdout.write(f'     AUTH_PARAMS: {providers.get("AUTH_PARAMS", {})}')
        
        # Check 4: URLs configuration
        self.stdout.write(self.style.HTTP_INFO('\n4️⃣  Checking URL configuration...'))
        from django.urls import resolve
        try:
            resolve('/dj-rest-auth/google/')
            self.stdout.write(self.style.SUCCESS('   ✓ /dj-rest-auth/google/ endpoint exists'))
        except:
            self.stdout.write(self.style.ERROR('   ✗ /dj-rest-auth/google/ endpoint not found'))
        
        try:
            resolve('/accounts/google/login/')
            self.stdout.write(self.style.SUCCESS('   ✓ /accounts/google/login/ endpoint exists'))
        except:
            self.stdout.write(self.style.ERROR('   ✗ /accounts/google/login/ endpoint not found'))
        
        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('📋 NEXT STEPS:'))
        self.stdout.write('=' * 60)
        self.stdout.write('\n1. Enable Google People API:')
        self.stdout.write('   https://console.cloud.google.com/apis/library/people.googleapis.com')
        self.stdout.write('\n2. Configure OAuth consent screen:')
        self.stdout.write('   https://console.cloud.google.com/apis/credentials/consent')
        self.stdout.write('\n3. Add redirect URIs:')
        protocol = 'https' if not settings.DEBUG else 'http'
        for site in app.sites.all():
            self.stdout.write(f'   {protocol}://{site.domain}/accounts/google/login/callback/')
        self.stdout.write('\n4. Test the web flow:')
        self.stdout.write(f'   {protocol}://{app.sites.first().domain}/accounts/google/login/')
        self.stdout.write('\n')
    
    def _test_token(self, token):
        """Test a Google access token."""
        
        # Test userinfo endpoint
        self.stdout.write('     Testing: https://www.googleapis.com/oauth2/v1/userinfo')
        try:
            resp = requests.get(
                'https://www.googleapis.com/oauth2/v1/userinfo',
                headers={'Authorization': f'Bearer {token}'}
            )
            
            if resp.status_code == 200:
                data = resp.json()
                self.stdout.write(self.style.SUCCESS(f'   ✓ Token is valid!'))
                self.stdout.write(f'     Email: {data.get("email", "N/A")}')
                self.stdout.write(f'     Name: {data.get("name", "N/A")}')
            elif resp.status_code == 401:
                self.stdout.write(self.style.ERROR('   ✗ Token is invalid or expired'))
                self.stdout.write(f'     Response: {resp.text}')
            elif resp.status_code == 403:
                self.stdout.write(self.style.ERROR('   ✗ Permission denied (API not enabled?)'))
                self.stdout.write(f'     Response: {resp.text}')
                self.stdout.write(self.style.WARNING('\n     🔴 ENABLE Google People API:'))
                self.stdout.write('        https://console.cloud.google.com/apis/library/people.googleapis.com')
            else:
                self.stdout.write(self.style.ERROR(f'   ✗ Unexpected response: {resp.status_code}'))
                self.stdout.write(f'     Response: {resp.text}')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Error: {str(e)}'))
