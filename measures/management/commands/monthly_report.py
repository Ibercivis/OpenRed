"""
Monthly OpenRed Radiación report (global analytics; sent around the 2nd for the previous month).

Usage:
    python manage.py monthly_report stats [--month YYYY-MM]
    python manage.py monthly_report test EMAIL [--month YYYY-MM]
    python manage.py monthly_report send [--month YYYY-MM] --yes
    python manage.py monthly_report preview [--month YYYY-MM] [--out DIR]   # writes charts as PNG, no email
"""
import os
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from measures.monthly_report import (
    build_report_email, compute_report, previous_month, render_report_charts, report_recipients, send_report,
)


class Command(BaseCommand):
    help = 'Informe mensual global de OpenRed Radiación'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        for name in ('stats', 'test', 'send', 'preview'):
            p = sub.add_parser(name)
            p.add_argument('--month', help='YYYY-MM (por defecto, el mes anterior)')
            if name == 'test':
                p.add_argument('to_email')
            if name == 'send':
                p.add_argument('--yes', action='store_true')
            if name == 'preview':
                p.add_argument('--out', required=True)

    def handle(self, *args, **options):
        try:
            month = date.fromisoformat(options['month'] + '-01') if options['month'] else previous_month()
        except ValueError:
            raise CommandError('--month debe ser YYYY-MM')
        r = compute_report(month)
        return getattr(self, f"handle_{options['action']}")(r, options)

    def _summary(self, r):
        w = self.stdout.write
        w(f'{r.month:%Y-%m}: histórico {r.total_n:,} medidas · {r.total_users} personas · {r.total_tracks} recorridos · {r.total_km:,.0f} km · {r.areas_total} zonas (+{r.areas_new})')
        w(f'  mes: {r.m} | anterior: {r.prev}')
        w(f'  dosis histórica {r.all_stats}')
        w(f'  dosis mes {r.month_stats}')
        w(f'  artefactos>{5}: {r.n_artefacts} · ruidosas: {r.n_noisy}')
        for z in r.zones:
            w(f"  zona {z['name']}: {z['mean']:.3f} µSv/h n={z['n']} tracks={z['tracks']} users={z['users']}")
        if r.low_zone:
            w(f"  zona baja {r.low_zone['name']}: {r.low_zone['mean']:.3f}")
        w(f'  máx validada: {r.max_reading}')
        w(f'  altitud: {[(a["label"], round(a["median"], 3), a["n"]) for a in r.altitude]}')
        w(f'  dispositivos {r.devices_total} (mes {r.devices_month}); modelos {r.models[:3]}')
        w(f'  hito: {r.milestone}')
        w(f'  destinatarios: {len(report_recipients())}')

    def handle_stats(self, r, options):
        self._summary(r)

    def handle_preview(self, r, options):
        os.makedirs(options['out'], exist_ok=True)
        for key, png in render_report_charts(r).items():
            if png:
                with open(os.path.join(options['out'], f'{key}.png'), 'wb') as f:
                    f.write(png)
                self.stdout.write(f'  {key}.png')

    def handle_test(self, r, options):
        build_report_email(r, options['to_email'], charts=render_report_charts(r)).send(fail_silently=False)
        self.stdout.write(self.style.SUCCESS(f"Informe de {r.month:%Y-%m} enviado SOLO a {options['to_email']}."))

    def handle_send(self, r, options):
        recipients = report_recipients()
        self._summary(r)
        if not options['yes']:
            if input(f'¿Enviar a {len(recipients)} personas? [escribe "si"]: ').strip().lower() not in ('si', 'sí', 'yes', 'y'):
                self.stdout.write('Cancelado.')
                return
        from measures import email_runs
        from measures.models import EmailRun
        period = f'{r.month:%Y-%m}'
        run, _ = EmailRun.objects.get_or_create(kind='monthly_report', period=period)
        result = send_report(r, recipients, run=run, period=period)
        email_runs.finish_run(run, len(recipients), result['sent'], result['failed'])
        self.stdout.write(self.style.SUCCESS(f"Enviados {result['sent']}, ya enviados antes {result['skipped']}, fallos {result['failed']}"))
        for e in result['errors']:
            self.stderr.write(e)
