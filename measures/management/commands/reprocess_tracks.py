"""
Re-parse the stored file of one or more tracks, replacing their measurements (and spectra).

Use it after a parser fix (e.g. RCTRK column layout / iOS export support) or
for tracks stuck in 'pending' / 'failed'. Each track is handled in its own
transaction: if parsing fails, its previous measurements are kept untouched.

Usage:
    python manage.py reprocess_tracks 445 449 506 507 509
    python manage.py reprocess_tracks 30 --dry-run        # parse only, no DB change
    python manage.py reprocess_tracks 413 --keep-status   # do not touch status on failure

Only 'rctrk' and 'json' tracks with a stored file are supported. Weather
bucketing for the new measurements is enqueued (best effort) like a normal
upload; the admin notification email is NOT sent.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from measures.models import RadiationMeasurement, Spectrum, Track
from measures.tasks import parse_json_track, parse_rctrk_track


class Rollback(Exception):
    """Raised inside the atomic block to discard a dry run."""


class Command(BaseCommand):
    help = "Re-parse the stored file of the given tracks and replace their measurements"

    def add_arguments(self, parser):
        parser.add_argument('track_ids', nargs='+', type=int)
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse and report the result, then roll everything back')
        parser.add_argument('--no-weather', action='store_true',
                            help='Do not enqueue weather bucketing for the new measurements')

    def handle(self, *args, **opts):
        failures = 0
        for track_id in opts['track_ids']:
            try:
                self.reprocess(track_id, dry_run=opts['dry_run'], weather=not opts['no_weather'])
            except CommandError as e:
                failures += 1
                self.stderr.write(self.style.ERROR(f"track {track_id}: {e}"))
        if failures:
            raise CommandError(f"{failures} track(s) could not be reprocessed")

    def reprocess(self, track_id, dry_run, weather):
        try:
            track = Track.objects.get(id=track_id)
        except Track.DoesNotExist:
            raise CommandError("does not exist")
        if not track.file:
            raise CommandError("has no stored file")
        if track.file_type not in ('rctrk', 'json'):
            raise CommandError(f"unsupported file_type {track.file_type!r}")

        before = RadiationMeasurement.objects.filter(track=track).count()
        try:
            with transaction.atomic():
                RadiationMeasurement.objects.filter(track=track).delete()
                # JSON uploads may carry spectra bound to the track; drop them too
                # so the re-parse does not duplicate them.
                Spectrum.objects.filter(track=track).delete()
                track.file.open('rb')
                try:
                    content = track.file.read()
                finally:
                    track.file.close()

                parser = parse_rctrk_track if track.file_type == 'rctrk' else parse_json_track
                created = parser(track, content)

                track.total_measurements = created
                track.status = 'completed'
                track.error_message = ''
                track.save(update_fields=['total_measurements', 'status', 'error_message'])
                track.refresh_from_db()

                self.stdout.write(
                    f"track {track_id} [{track.file_type}] {track.file.name.split('/')[-1]}: "
                    f"{before} -> {created} measurements | "
                    f"{track.start_time:%Y-%m-%d %H:%M} .. {track.end_time:%H:%M} | "
                    f"dist {round(track.total_distance or 0)} m | "
                    f"dose {track.min_dose_rate:.4f}/{track.avg_dose_rate:.4f}/{track.max_dose_rate:.4f} µSv/h"
                )
                if dry_run:
                    raise Rollback
        except Rollback:
            self.stdout.write(self.style.WARNING(f"track {track_id}: dry run, rolled back"))
            return
        except Exception as e:
            raise CommandError(f"parse failed, previous {before} measurements kept: {e}")

        self.stdout.write(self.style.SUCCESS(f"track {track_id}: done"))
        if weather:
            self.enqueue_weather(track_id, created)

    def enqueue_weather(self, track_id, created):
        try:
            import django_rq
            queue = django_rq.get_queue('openred-weather')
            job = queue.enqueue(
                'measures.tasks.assign_pending_weather_buckets',
                limit=created + 50,
                job_timeout='30m',
                result_ttl=3600,
                job_id=f'weather_buckets_track_{track_id}_{timezone.now().timestamp()}',
            )
            self.stdout.write(f"track {track_id}: weather bucketing enqueued ({job.id})")
        except Exception as e:
            self.stderr.write(self.style.WARNING(f"track {track_id}: weather enqueue failed: {e}"))
