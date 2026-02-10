"""
Management command to initialize RQ scheduled jobs from settings.

This command reads RQ_JOBS from settings.py and schedules them in Redis.
Run this once after deployment or settings changes.

Usage:
    python manage.py init_rq_scheduler
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from django_rq import get_scheduler
from datetime import datetime, timedelta
import importlib


class Command(BaseCommand):
    help = 'Initialize RQ scheduled jobs from settings.RQ_JOBS'

    def handle(self, *args, **options):
        scheduler = get_scheduler('openred-weather')
        
        self.stdout.write(
            self.style.SUCCESS('\n⚙️  Initializing RQ scheduled jobs from settings...\n')
        )
        
        # Get jobs from settings
        rq_jobs = getattr(settings, 'RQ_JOBS', {})
        
        if not rq_jobs:
            self.stdout.write(
                self.style.WARNING('⚠️  No RQ_JOBS found in settings.py\n')
            )
            return
        
        # Clear existing jobs
        self.stdout.write('  Clearing existing scheduled jobs...')
        existing_jobs = scheduler.get_jobs()
        cleared = 0
        for job in existing_jobs:
            scheduler.cancel(job)
            cleared += 1
        if cleared > 0:
            self.stdout.write(self.style.WARNING(f'  ✓ Cleared {cleared} existing job(s)'))
        
        # Schedule each job
        scheduled_count = 0
        for job_name, job_config in rq_jobs.items():
            try:
                # Parse function path
                func_path = job_config['func']
                module_path, func_name = func_path.rsplit('.', 1)
                module = importlib.import_module(module_path)
                func = getattr(module, func_name)
                
                # Get job parameters
                kwargs = job_config.get('kwargs', {})
                interval = job_config.get('interval', 3600)
                repeat = job_config.get('repeat', None)
                timeout = job_config.get('timeout', 300)
                result_ttl = job_config.get('result_ttl', 500)
                
                # Schedule first run in 10 seconds
                scheduled_time = datetime.utcnow() + timedelta(seconds=10)
                
                job = scheduler.schedule(
                    scheduled_time=scheduled_time,
                    func=func,
                    kwargs=kwargs,
                    interval=interval,
                    repeat=repeat,
                    timeout=timeout,
                    result_ttl=result_ttl,
                    id=job_name  # Use job name as ID
                )
                
                scheduled_count += 1
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'\n✓ Scheduled: {job_name}\n'
                        f'  Function: {func_path}\n'
                        f'  First run: {scheduled_time.strftime("%Y-%m-%d %H:%M:%S UTC")} (in 10 seconds)\n'
                        f'  Interval: Every {interval} seconds ({interval/60:.1f} minutes)\n'
                        f'  Kwargs: {kwargs}\n'
                    )
                )
                
                if interval == 60:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  ⚠️  Testing mode: Interval is 1 minute\n'
                            f'     For production, change interval to 3600 (1 hour) in settings.py\n'
                        )
                    )
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'\n✗ Failed to schedule {job_name}: {str(e)}\n'
                    )
                )
        
        if scheduled_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✓ Successfully scheduled {scheduled_count} job(s)\n'
                    f'\n💡 To run the scheduler:\n'
                    f'   python manage.py rqscheduler\n'
                    f'\n💡 To run workers:\n'
                    f'   python manage.py rqworker openred-tracks  (for track processing)\n'
                    f'   python manage.py rqworker openred-weather  (for weather data)\n'
                    f'\n💡 To view scheduled jobs:\n'
                    f'   python manage.py rqstats\n'
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING('\n⚠️  No jobs were scheduled\n')
            )
