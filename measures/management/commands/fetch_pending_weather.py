"""
Management command to fetch weather data for pending WeatherCache entries.

This command can be run manually or scheduled via cron/systemd to periodically
fetch weather data from Open-Meteo API for all WeatherCache entries that haven't
been fetched yet.

Usage:
    python manage.py fetch_pending_weather [--limit 100] [--max-attempts 3]

Examples:
    # Fetch up to 100 pending entries (default)
    python manage.py fetch_pending_weather
    
    # Fetch up to 500 entries
    python manage.py fetch_pending_weather --limit 500
    
    # Process entries with up to 5 failed attempts
    python manage.py fetch_pending_weather --max-attempts 5

Scheduling (crontab example):
    # Run every hour
    0 * * * * cd /path/to/openred-api && source .venv/bin/activate && python manage.py fetch_pending_weather >> /var/log/weather_fetch.log 2>&1
"""
from django.core.management.base import BaseCommand
from measures.tasks import fetch_pending_weather


class Command(BaseCommand):
    help = 'Fetch weather data for pending WeatherCache entries from Open-Meteo API'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum number of WeatherCache entries to process (default: 100)'
        )
        parser.add_argument(
            '--max-attempts',
            type=int,
            default=3,
            help='Skip entries that have failed this many times (default: 3)'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        max_attempts = options['max_attempts']
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nFetching weather data (limit={limit}, max_attempts={max_attempts})...\n'
            )
        )
        
        # Run the fetch task
        result = fetch_pending_weather(limit=limit, max_attempts=max_attempts)
        
        # Display results
        if result['success']:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Weather fetch completed successfully!\n"
                    f"  Processed: {result['caches_processed']} entries\n"
                    f"  Updated: {result['caches_updated']} entries\n"
                    f"  Skipped: {result.get('caches_skipped', 0)} entries (max attempts)\n"
                )
            )
            
            if result.get('errors'):
                self.stdout.write(
                    self.style.WARNING(
                        f"\n⚠ Encountered {len(result['errors'])} errors:\n"
                    )
                )
                for error in result['errors'][:10]:  # Show first 10 errors
                    self.stdout.write(f"  - {error}")
                if len(result['errors']) > 10:
                    self.stdout.write(f"  ... and {len(result['errors']) - 10} more")
        else:
            self.stdout.write(
                self.style.ERROR(
                    f"\n✗ Weather fetch failed: {result.get('error', 'Unknown error')}\n"
                )
            )
            return
        
        self.stdout.write('\n')
