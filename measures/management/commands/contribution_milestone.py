"""
Contribution milestones (300k / 400k / ...): read-only status.

The milestone email (detection job + manual send) was removed: the monthly report now shows the
progress towards the next milestone and announces the ones crossed during the month. The
ContributionMilestone table is kept as a record of what was sent.

Usage:
    python manage.py contribution_milestone status
"""
from django.core.management.base import BaseCommand

from measures.models import ContributionMilestone
from measures.tasks import count_project_contributions, milestone_thresholds_reached


class Command(BaseCommand):
    help = 'Estado de los hitos de contribuciones (solo lectura)'

    def add_arguments(self, parser):
        parser.add_argument('action', nargs='?', default='status', choices=['status'])

    def handle(self, *args, **options):
        total = count_project_contributions()
        self.stdout.write(f'Contribuciones (proyecto del hito): {total:,}')
        self.stdout.write(f'Umbrales alcanzados: {milestone_thresholds_reached(total) or "ninguno"}')
        self.stdout.write('Registro histórico de hitos (correos ya enviados):')
        for m in ContributionMilestone.objects.order_by('threshold'):
            sent = f'enviado {m.sent_at:%Y-%m-%d %H:%M} a {m.recipients_count} ({m.failed_count} fallos)' if m.sent_at else ''
            self.stdout.write(f'  {m.threshold:>9,}  {m.status:<10}  detectado {m.detected_at:%Y-%m-%d %H:%M}  {sent}')
        if not ContributionMilestone.objects.exists():
            self.stdout.write('  (ninguno)')
