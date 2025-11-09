"""
Management command to setup the weather fetching scheduler.

This command configures RQ scheduler to periodically fetch weather data
for pending WeatherCache entries.

Usage:
    # Setup with 1 minute interval (for testing)
    python manage.py setup_weather_scheduler --interval 60
    
    # Setup with 1 hour interval (production)
    python manage.py setup_weather_scheduler --interval 3600
    
    # List current scheduled jobs
    python manage.py setup_weather_scheduler --list
    
    # Clear all weather scheduler jobs
    python manage.py setup_weather_scheduler --clear
"""
from django.core.management.base import BaseCommand
from django_rq import get_scheduler
from datetime import datetime, timedelta


class Command(BaseCommand):
    help = 'Setup RQ scheduler for periodic weather data fetching'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=60,
            help='Interval in seconds between fetches (default: 60 for testing, use 3600 for production)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum WeatherCache entries to process per run (default: 100)'
        )
        parser.add_argument(
            '--max-attempts',
            type=int,
            default=3,
            help='Skip entries with this many failed attempts (default: 3)'
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help='List current scheduled jobs'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear all weather fetching scheduled jobs'
        )

    def handle(self, *args, **options):
        scheduler = get_scheduler('default')
        
        # List scheduled jobs
        if options['list']:
            self.stdout.write(self.style.SUCCESS('\n📋 Current scheduled jobs:\n'))
            jobs = scheduler.get_jobs()
            if not jobs:
                self.stdout.write('  No scheduled jobs found.\n')
            else:
                for job in jobs:
                    self.stdout.write(f"  - {job.id}: {job.func_name}")
                    self.stdout.write(f"    Next run: {job.meta.get('scheduled_time', 'Unknown')}")
                    self.stdout.write(f"    Interval: {job.meta.get('interval', 'N/A')} seconds\n")
            return
        
        # Clear weather scheduler jobs
        if options['clear']:
            self.stdout.write(self.style.WARNING('\n🗑️  Clearing weather scheduler jobs...\n'))
            cleared = 0
            for job in scheduler.get_jobs():
                if 'fetch_pending_weather' in str(job.func_name):
                    scheduler.cancel(job)
                    cleared += 1
                    self.stdout.write(f"  ✓ Cancelled job: {job.id}")
            self.stdout.write(
                self.style.SUCCESS(f"\n✓ Cleared {cleared} weather scheduler job(s)\n")
            )
            return
        
        # Setup scheduler
        interval = options['interval']
        limit = options['limit']
        max_attempts = options['max_attempts']
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n⚙️  Setting up weather fetching scheduler...\n'
                f'  Interval: {interval} seconds ({interval/60:.1f} minutes)\n'
                f'  Limit: {limit} entries per run\n'
                f'  Max attempts: {max_attempts}\n'
            )
        )
        
        # Clear existing weather fetch jobs first
        self.stdout.write('  Clearing existing weather scheduler jobs...')
        cleared = 0
        for job in scheduler.get_jobs():
            if 'fetch_pending_weather' in str(job.func_name):
                scheduler.cancel(job)
                cleared += 1
        if cleared > 0:
            self.stdout.write(self.style.WARNING(f'  ✓ Cleared {cleared} existing job(s)'))
        
        # Schedule new job
        from measures.tasks import fetch_pending_weather
        
        scheduled_time = datetime.utcnow() + timedelta(seconds=10)  # First run in 10 seconds
        
        job = scheduler.schedule(
            scheduled_time=scheduled_time,
            func=fetch_pending_weather,
            kwargs={
                'limit': limit,
                'max_attempts': max_attempts
            },
            interval=interval,
            repeat=None,  # Repeat indefinitely
            result_ttl=500,  # Keep results for 500 seconds
            timeout=300  # 5 minute timeout per job
        )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n✓ Scheduler configured successfully!\n'
                f'  Job ID: {job.id}\n'
                f'  First run: {scheduled_time.strftime("%Y-%m-%d %H:%M:%S UTC")} (in 10 seconds)\n'
                f'  Interval: Every {interval} seconds\n'
            )
        )
        
        if interval == 60:
            self.stdout.write(
                self.style.WARNING(
                    f'\n⚠️  Testing mode: Interval is 1 minute\n'
                    f'   For production, use: --interval 3600 (1 hour)\n'
                )
            )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n💡 Monitor the scheduler:\n'
                f'   python manage.py setup_weather_scheduler --list\n'
                f'   python manage.py rqworker default  (in another terminal)\n'
                f'\n💡 Clear the scheduler:\n'
                f'   python manage.py setup_weather_scheduler --clear\n'
            )
        )
