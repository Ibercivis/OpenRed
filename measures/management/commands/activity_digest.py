"""
Radiation activity digest (monthly email with each contributor's own stats).

Usage:
    python manage.py activity_digest stats [--lifetime] [--audience all|recent]
        Project figures and who would get which variant. Sends nothing.

    python manage.py activity_digest test EMAIL [--as-user USERNAME|EMAIL] [--miss] [--lifetime]
        Send ONE email to EMAIL, rendered with the data of --as-user (default:
        the user owning EMAIL). --miss forces the "te echamos de menos" variant.

    python manage.py activity_digest send [--lifetime] [--audience all|recent] --yes
        Send to every recipient. First mailing: --lifetime --audience all.
        Monthly: defaults (last 30 days, people active in the last 60).
"""
import copy

from django.core.management.base import BaseCommand, CommandError

from measures.activity_digest import (
    build_digest_email, compute_digest, digest_recipients, render_user_charts, send_digest,
)


class Command(BaseCommand):
    help = 'Estadísticas, prueba y envío del resumen mensual de actividad (radiación)'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        for name in ('stats', 'test', 'send'):
            p = sub.add_parser(name)
            p.add_argument('--lifetime', action='store_true', help='Estadísticas desde el inicio (primer envío)')
            p.add_argument('--days', type=int, default=30)
            p.add_argument('--audience', choices=['all', 'recent'], default=None,
                           help='all = todos los contribuyentes; recent = activos en los últimos 60 días '
                                '(por defecto: all con --lifetime, recent sin él)')
            if name == 'test':
                p.add_argument('to_email')
                p.add_argument('--as-user', help='username o email cuyos datos se usan')
                p.add_argument('--miss', action='store_true', help='Forzar la variante "te echamos de menos"')
            if name == 'send':
                p.add_argument('--yes', action='store_true')
                p.add_argument('--period', help='YYYY-MM con el que se registra el envio (por defecto, el mes actual)')
                p.add_argument('--only', help='Lista de usernames separados por comas: enviar solo a esos')

    def handle(self, *args, **options):
        digest = compute_digest(days=options['days'], lifetime=options['lifetime'])
        audience = options['audience'] or ('all' if options['lifetime'] else 'recent')
        return getattr(self, f"handle_{options['action']}")(digest, audience, options)

    def _summary(self, digest, recipients):
        w = self.stdout.write
        w(f'Proyecto {digest.project_name}: {digest.since:%Y-%m-%d} -> {digest.until:%Y-%m-%d} '
          f'({"histórico" if digest.lifetime else "ventana"})')
        w(f'  {digest.n:,} medidas · {digest.n_users} personas · {digest.n_tracks} recorridos · {digest.km:,.0f} km · '
          f'mediana {digest.median:.4f} µSv/h')
        w(f'  Últimos {digest.days} días: {digest.recent_n:,} medidas · {digest.n_recent_users} personas · {digest.recent_km:,.0f} km')
        active = [u for u in recipients if u.active]
        miss = [u for u in recipients if not u.active]
        w(f'Destinatarios: {len(recipients)} -> {len(active)} resumen · {len(miss)} "te echamos de menos"')
        for u in recipients:
            tag = 'ACTIVO' if u.active else 'MISS  '
            med = f'{u.median:.3f}' if u.median is not None else '  -  '
            mx = f'{u.max_ok:.3f}' if u.max_ok is not None else '  -  '
            w(f'  {tag} {u.display_name[:18]:<18} {u.email[:32]:<32} n={u.n:>6} últ{digest.days}d={u.recent_n:>6} '
              f'km={u.km:>7.1f} med={med} max_ok={mx} hora={u.usual_hour}')

    def handle_stats(self, digest, audience, options):
        self._summary(digest, digest_recipients(digest, audience))

    def handle_test(self, digest, audience, options):
        ident = options.get('as_user') or options['to_email']
        match = [u for u in digest.users.values()
                 if ident.lower() in (u.email.lower(), u.display_name.lower())]
        if not match:
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.filter(username__iexact=ident).first()
            match = [digest.users[user.id]] if user and user.id in digest.users else []
        if not match:
            raise CommandError(f'No hay contribuyente con datos para "{ident}"')
        user = copy.copy(match[0])
        if options['miss']:
            user.recent_n = 0
        charts = render_user_charts(user, digest)
        build_digest_email(digest, user, options['to_email'], charts=charts).send(fail_silently=False)
        self.stdout.write(self.style.SUCCESS(
            f'Enviado a {options["to_email"]} con datos de {user.display_name} '
            f'({"resumen" if user.active else "te echamos de menos"}).'))

    def handle_send(self, digest, audience, options):
        recipients = digest_recipients(digest, audience)
        if options.get('only'):
            wanted = {x.strip().lower() for x in options['only'].split(',') if x.strip()}
            recipients = [u for u in recipients if u.username.lower() in wanted]
            missing = wanted - {u.username.lower() for u in recipients}
            if missing:
                raise CommandError(f'No son destinatarios: {sorted(missing)}')
        self._summary(digest, recipients)
        if not options['yes']:
            if input('¿Confirmar envío? [escribe "si"]: ').strip().lower() not in ('si', 'sí', 'yes', 'y'):
                self.stdout.write('Cancelado.')
                return
        from measures import email_runs
        from measures.models import EmailRun
        period = options.get('period') or email_runs.local_now().strftime('%Y-%m')
        run, _ = EmailRun.objects.get_or_create(kind='activity_digest', period=period)
        result = send_digest(digest, recipients, run=run, period=period)
        email_runs.finish_run(run, len(recipients), result['sent'], result['failed'], notes=run.notes)
        self.stdout.write(self.style.SUCCESS(f"Enviados {result['sent']}, ya enviados antes {result['skipped']}, fallos {result['failed']}"))
        for e in result['errors']:
            self.stderr.write(e)
