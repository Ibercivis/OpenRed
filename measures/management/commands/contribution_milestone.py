"""
Manage contribution milestones (300k / 400k / ... measurements) and their
bilingual thank-you email.

Usage:
    python manage.py contribution_milestone status
        Current count, thresholds reached and what is recorded in the DB.

    python manage.py contribution_milestone check
        Run the detector now (same as the scheduled RQ job), inline.

    python manage.py contribution_milestone test frasanz@ibercivis.es [--threshold 400000]
        Send the email ONLY to that address (no DB change). Defaults to the
        highest threshold reached.

    python manage.py contribution_milestone send --threshold 400000 [--yes] [--inline]
        Send the email for that threshold to every contributor of the project,
        through the worker queue (or in-process with --inline). Use it for
        milestones recorded as 'backfilled'.
"""
from django.core.management.base import BaseCommand, CommandError

from measures.models import ContributionMilestone
from measures.tasks import (
    build_milestone_email,
    check_contribution_milestones,
    count_project_contributions,
    milestone_recipients,
    milestone_thresholds_reached,
)


class Command(BaseCommand):
    help = 'Estado, prueba y envío del correo de hitos de contribuciones'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('status')
        sub.add_parser('check')
        p_test = sub.add_parser('test')
        p_test.add_argument('to_email')
        p_test.add_argument('--threshold', type=int)
        p_send = sub.add_parser('send')
        p_send.add_argument('--threshold', type=int, required=True)
        p_send.add_argument('--yes', action='store_true', help='No pedir confirmación')
        p_send.add_argument('--inline', action='store_true',
                            help='Enviar en este proceso en vez de encolarlo al worker')

    def handle(self, *args, **options):
        return getattr(self, f"handle_{options['action']}")(options)

    def handle_status(self, options):
        total = count_project_contributions()
        reached = milestone_thresholds_reached(total)
        self.stdout.write(f'Contribuciones (proyecto del hito): {total:,}')
        self.stdout.write(f'Umbrales alcanzados: {reached or "ninguno"}')
        self.stdout.write(f'Destinatarios (contribuyentes activos con email): {len(milestone_recipients())}')
        self.stdout.write('Hitos registrados:')
        for m in ContributionMilestone.objects.order_by('threshold'):
            self.stdout.write(f'  {m.threshold:>9,}  {m.status:<10}  detectado {m.detected_at:%Y-%m-%d %H:%M}  '
                              f'enviado {m.sent_at:%Y-%m-%d %H:%M} a {m.recipients_count} ({m.failed_count} fallos)'
                              if m.sent_at else
                              f'  {m.threshold:>9,}  {m.status:<10}  detectado {m.detected_at:%Y-%m-%d %H:%M}')
        if not ContributionMilestone.objects.exists():
            self.stdout.write('  (ninguno)')

    def handle_check(self, options):
        result = check_contribution_milestones()
        self.stdout.write(self.style.SUCCESS(f'check_contribution_milestones -> {result}'))

    def handle_test(self, options):
        threshold = options['threshold']
        if threshold is None:
            reached = milestone_thresholds_reached(count_project_contributions())
            if not reached:
                raise CommandError('Ningún umbral alcanzado todavía; indica --threshold')
            threshold = reached[-1]
        to_email = options['to_email']
        self.stdout.write(f'Enviando correo de prueba del hito {threshold:,} a {to_email}...')
        build_milestone_email(threshold, to_email).send(fail_silently=False)
        self.stdout.write(self.style.SUCCESS('Enviado.'))

    def handle_send(self, options):
        threshold = options['threshold']
        milestone, created = ContributionMilestone.objects.get_or_create(
            threshold=threshold,
            defaults={'total_at_detection': count_project_contributions(), 'status': 'pending'},
        )
        if milestone.status in ('sent', 'sending'):
            raise CommandError(f'El hito {threshold:,} ya está en estado {milestone.status}')
        recipients = milestone_recipients()
        self.stdout.write(f'Hito {threshold:,} -> se enviará a {len(recipients)} usuarios.')
        if not options['yes']:
            answer = input('¿Confirmar envío a todos los usuarios? [escribe "si"]: ')
            if answer.strip().lower() not in ('si', 'sí', 'yes', 'y'):
                self.stdout.write('Cancelado.')
                return
        milestone.status = 'pending'
        milestone.save(update_fields=['status'])
        if options['inline']:
            from measures.tasks import send_contribution_milestone_email
            result = send_contribution_milestone_email(milestone.id)
            self.stdout.write(self.style.SUCCESS(f'Enviado: {result}'))
            return
        import django_rq
        job = django_rq.get_queue('openred-tracks').enqueue(
            'measures.tasks.send_contribution_milestone_email',
            milestone.id,
            job_timeout='30m',
            result_ttl=86400,
            job_id=f'milestone_email_{threshold}_manual',
        )
        self.stdout.write(self.style.SUCCESS(f'Encolado job {job.id} en openred-tracks. Sigue el estado con: contribution_milestone status'))
