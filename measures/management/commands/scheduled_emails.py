"""
Inspect and drive the periodic emails (monthly report on day 2, personal digest on day 16).

Usage:
    python manage.py scheduled_emails status
        Switches, last runs and per-recipient log counts.

    python manage.py scheduled_emails check
        Run the scheduler decision now (what the hourly job does) and print the outcome.

    python manage.py scheduled_emails backfill-digest --period YYYY-MM --yes
        Record the first (manual, lifetime) personal mailing in the log WITHOUT sending anything,
        so the scheduled job does not repeat it. Recipients = the current "all" audience.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from measures import email_runs
from measures.models import EmailLog, EmailRun


class Command(BaseCommand):
    help = 'Estado y control de los correos periódicos'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('status')
        sub.add_parser('check')
        p = sub.add_parser('backfill-digest')
        p.add_argument('--period', required=True)
        p.add_argument('--yes', action='store_true')

    def handle(self, *args, **options):
        return getattr(self, f"handle_{options['action'].replace('-', '_')}")(options)

    def handle_status(self, options):
        w = self.stdout.write
        w(f"MONTHLY_REPORT_ENABLED={settings.MONTHLY_REPORT_ENABLED}  ACTIVITY_DIGEST_ENABLED={settings.ACTIVITY_DIGEST_ENABLED}")
        w(f"Ventanas: informe dias {email_runs.REPORT_DAY}-{email_runs.REPORT_LAST_DAY}, "
          f"personal dias {email_runs.DIGEST_DAY}-{email_runs.DIGEST_LAST_DAY}, "
          f"horas {email_runs.SEND_HOUR_FROM}-{email_runs.SEND_HOUR_TO} ({email_runs.TZ})")
        for run in EmailRun.objects.order_by('-period', 'kind')[:12]:
            w(f"  {run.period} {run.kind:<16} {run.status:<8} destinatarios={run.recipients_count} "
              f"enviados={run.sent_count} fallos={run.failed_count}")
        if not EmailRun.objects.exists():
            w('  (sin ejecuciones)')
        w(f"EmailLog: {EmailLog.objects.filter(status='sent').count()} enviados, "
          f"{EmailLog.objects.filter(status='failed').count()} fallidos")

    def handle_check(self, options):
        from measures.tasks import check_scheduled_emails
        self.stdout.write(str(check_scheduled_emails()))

    def handle_backfill_digest(self, options):
        from measures.activity_digest import compute_digest, digest_recipients
        period = options['period']
        digest = compute_digest(lifetime=True)
        recipients = digest_recipients(digest, 'all')
        self.stdout.write(f'Se registrarian {len(recipients)} destinatarios como enviados en {period} (sin enviar nada).')
        if not options['yes']:
            self.stdout.write('Usa --yes para confirmar.')
            return
        run, _ = EmailRun.objects.get_or_create(kind='activity_digest', period=period)
        for u in recipients:
            email_runs.log_email('activity_digest', period, u.email, 'sent', run=run, user_id=u.user_id,
                                 variant='active' if u.active else 'miss',
                                 subject='(backfill del primer envio manual, modo historico)',
                                 payload={'backfill': True, 'mode': 'lifetime', 'n': u.n, 'total_n': u.total_n})
        email_runs.finish_run(run, len(recipients), len(recipients), 0, notes='Primer envio manual (historico), registrado a posteriori')
        self.stdout.write(self.style.SUCCESS(f'Registrados {len(recipients)}. Ejecucion: {run}'))
