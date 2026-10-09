"""
Thank-you email with statistics for the light-pollution contributors of an
area and period (e.g. Zaragoza, last 15 days). Replicable for any city/date.

Usage:
    python manage.py light_area_summary stats --city zaragoza --radius-km 120 --days 15
        Print the statistics and the recipients. Nothing is sent.
        Area: --city NAME (see CITIES) or --center LAT,LON [--place "Nombre"].
        Period: --days N (ending today) or --since YYYY-MM-DD [--until YYYY-MM-DD]
        (local dates, inclusive, in --tz, default Europe/Madrid).

    python manage.py light_area_summary test tu@correo.org --city zaragoza ...
        Send the email ONLY to that address, with the personal block of the
        top contributor (or of --as-user USERNAME) so the layout can be checked.
        With --no-personal it is an informative copy for a third party.

    python manage.py light_area_summary send --city zaragoza ... [--yes] [--only USER,...]
        Send one personalised email to every contributor in the area/period.

Options shared by all actions: --lang es, --project NAME (default all), --twilight -6,
--rings 2,6, --no-rest (skip the comparison with the rest of OpenRed),
--save-html PATH (write the rendered HTML of the first message, for review).
"""
import os
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from measures.light_area_summary import (
    CITIES,
    CITY_ZONES,
    CITY_ZONES_SOURCES,
    NEIGHBOURHOOD_MIN_NIGHT,
    ZONES_DIR,
    fetch_city_zones,
    load_zones,
    REST_PLACE_MIN_NIGHT,
    REST_PLACE_MIN_TRACKS,
    DEFAULT_RINGS,
    DEFAULT_TWILIGHT_ELEVATION,
    build_area_summary_email,
    compute_area_summary,
    render_area_summary_charts,
    send_area_summary,
)


class Command(BaseCommand):
    help = 'Correo de agradecimiento con estadísticas de contaminación lumínica por zona y periodo'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        parsers = {
            'stats': sub.add_parser('stats'),
            'test': sub.add_parser('test'),
            'send': sub.add_parser('send'),
        }
        p_fetch = sub.add_parser('fetch-zones')
        p_fetch.add_argument('--city', required=True, choices=sorted(CITY_ZONES_SOURCES))
        parsers['test'].add_argument('to_email')
        parsers['test'].add_argument('--as-user', help='Username cuyo bloque personal se muestra en la prueba')
        parsers['test'].add_argument('--no-personal', action='store_true',
                                     help='Sin bloque personal: copia informativa para terceros (asociaciones, prensa...)')
        parsers['send'].add_argument('--yes', action='store_true', help='No pedir confirmación')
        parsers['send'].add_argument('--only', help='Enviar solo a estos usernames (separados por comas)')
        for p in parsers.values():
            area = p.add_mutually_exclusive_group(required=True)
            area.add_argument('--city', choices=sorted(CITIES), help='Centro predefinido')
            area.add_argument('--center', help='LAT,LON del centro')
            p.add_argument('--place', help='Nombre a mostrar en el correo (por defecto el de --city)')
            p.add_argument('--radius-km', type=float, default=100.0)
            p.add_argument('--days', type=int, help='Últimos N días (incluido hoy)')
            p.add_argument('--since', help='Fecha local inicial YYYY-MM-DD')
            p.add_argument('--until', help='Fecha local final YYYY-MM-DD (por defecto hoy)')
            p.add_argument('--compare-until', help='Fecha local final del periodo anterior (mismo --since): '
                           'el correo muestra los cambios desde entonces')
            p.add_argument('--tz', default='Europe/Madrid')
            p.add_argument('--lang', default='es')
            p.add_argument('--project', help='Limitar a un proyecto (por defecto, todos)')
            p.add_argument('--twilight', type=float, default=DEFAULT_TWILIGHT_ELEVATION,
                           help='Elevación solar máxima (grados) para contar una medida como nocturna')
            p.add_argument('--rings', default=','.join(str(r) for r in DEFAULT_RINGS),
                           help='Radios (km) de los anillos centro/ciudad/exterior')
            p.add_argument('--no-rest', action='store_true', help='Sin comparación con el resto de OpenRed')
            p.add_argument('--zones', help='GeoJSON de barrios/distritos (WGS84)')
            p.add_argument('--zones-name-field', default='name', help='Propiedad con el nombre del barrio')
            p.add_argument('--no-zones', action='store_true', help='Sin desglose por barrios')
            p.add_argument('--save-html', help='Guardar el HTML del primer correo en esta ruta')

    # ------------------------------------------------------------------
    def _zones(self, o):
        if o['no_zones']:
            return None
        path = o['zones']
        if not path and o['city'] and o['city'] in CITY_ZONES:
            path = os.path.join(ZONES_DIR, CITY_ZONES[o['city']])
        if not path:
            return None
        if not os.path.exists(path):
            raise CommandError(f'No existe {path}' + (' (ejecuta fetch-zones)' if not o['zones'] else ''))
        try:
            zones = load_zones(path, o['zones_name_field'])
        except ValueError as e:
            raise CommandError(str(e))
        self.stdout.write(f'Barrios: {len(zones)} polígonos de {path}')
        return zones

    def _summary(self, o):
        if o['city']:
            lat, lon, name = CITIES[o['city']]
        else:
            try:
                lat, lon = (float(x) for x in o['center'].split(','))
            except ValueError:
                raise CommandError('--center debe ser LAT,LON')
            name = None
        place = o['place'] or name
        if not place:
            raise CommandError('Indica --place cuando uses --center')

        today = date.today()
        if o['days']:
            until = today
            since = today - timedelta(days=o['days'] - 1)
        elif o['since']:
            since = date.fromisoformat(o['since'])
            until = date.fromisoformat(o['until']) if o['until'] else today
        else:
            raise CommandError('Indica --days N o --since YYYY-MM-DD')
        if since > until:
            raise CommandError('--since posterior a --until')
        try:
            rings = tuple(float(x) for x in o['rings'].split(',') if x.strip())
        except ValueError:
            raise CommandError('--rings debe ser una lista de km, p.ej. 2,6')

        self.stdout.write(f'Zona: {place} ({lat:.4f}, {lon:.4f}) radio {o["radius_km"]:g} km · '
                          f'{since} → {until} ({o["tz"]}) · proyecto {o["project"] or "todos"}')
        zones = self._zones(o)
        common = dict(place_name=place, project_name=o['project'], tz=o['tz'],
                      twilight_elevation=o['twilight'], rings=rings, zones=zones)
        try:
            summary = compute_area_summary((lat, lon), o['radius_km'], since, until,
                                           compare_rest=not o['no_rest'], **common)
            if o['compare_until']:
                prev_until = date.fromisoformat(o['compare_until'])
                if not since <= prev_until < until:
                    raise CommandError('--compare-until debe estar entre --since y --until (excluido)')
                summary.baseline = compute_area_summary((lat, lon), o['radius_km'], since, prev_until,
                                                        compare_rest=False, **common)
                self.stdout.write(f'Cambios respecto al periodo {since} → {prev_until}: '
                                  f'{summary.baseline.n_measurements:,} medidas, {summary.baseline.n_users} personas')
            return summary
        except ValueError as e:
            raise CommandError(str(e))

    def _print_stats(self, s):
        st = s.stats
        w = self.stdout.write
        w(f'Medidas: {s.n_measurements:,} · nocturnas: {st.n_night:,} · tracks: {s.n_tracks} · '
          f'km: {s.km:,.1f} · personas: {s.n_users} · noches: {s.n_nights} · '
          f'dist. máx: {s.max_distance_km:.1f} km')
        if s.first_local:
            w(f'Primera/última medida (local): {s.first_local:%Y-%m-%d %H:%M} / {s.last_local:%Y-%m-%d %H:%M}')
        if st.n_lux:
            w(f'Lux: mediana {st.lux_median:.1f} (p25 {st.lux_p25:.1f}, p75 {st.lux_p75:.1f}, media {st.lux_mean:.1f}) · '
              f'>30 lux: {st.lux_pct_over_30}%')
            w('  ' + ' · '.join(f"{b['label']} {b['pct']}%" for b in st.lux_bands))
        if st.n_cct:
            w(f'CCT: mediana {st.cct_median:.0f} K · ≤2700: {st.cct_pct_le_2700}% · ≤3000: {st.cct_pct_le_3000}% · '
              f'>4000: {st.cct_pct_over_4000}% (válidas {st.n_cct:,})')
        for label, r in s.rings:
            if r.n_lux:
                w(f'  {label}: {r.n_lux:,} medidas · {r.lux_median:.1f} lux · '
                  f'{r.cct_median:.0f} K · >30 lux {r.lux_pct_over_30}%' if r.n_cct else
                  f'  {label}: {r.n_lux:,} medidas · {r.lux_median:.1f} lux')
        if s.neighbourhoods:
            w(f'Por barrios ({s.n_outside_zones:,} medidas fuera de todos los polígonos):')
            for h in s.neighbourhoods:
                st = h['stats']
                if not st.n_lux:
                    continue
                mark = '' if st.n_lux >= NEIGHBOURHOOD_MIN_NIGHT else '   (no sale en el correo)'
                w(f'  {h["name"]:<26} {st.n_lux:>6,} noct. {h["n_users"]:>2} pers. · {st.lux_median:.1f} lux · '
                  + (f'{st.cct_median:.0f} K' if st.n_cct else '-') + f' · >30 lux {st.lux_pct_over_30}%{mark}')
        if s.weather.get('temperature') is not None:
            w(f'Meteo: {s.weather["temperature"]:.1f} °C · humedad {s.weather.get("humidity", 0):.0f}% · '
              f'nubes {s.weather.get("cloud_cover", 0):.0f}% · viento {s.weather.get("wind_speed", 0):.1f} km/h '
              f'(cobertura {s.weather.get("coverage_pct")}%)')
        from measures.urban_class import GROUP_LABELS_ES, GROUP_ORDER
        if s.zones:
            w('Por tipo de zona (GHS-SMOD), esta área:')
            for g in GROUP_ORDER:
                z = s.zones.get(g)
                if z and z.n_lux:
                    w(f'  {GROUP_LABELS_ES[g]:<24} {z.n_lux:>6,} medidas · {z.lux_median:.1f} lux · '
                      + (f'{z.cct_median:.0f} K' if z.n_cct else '-'))
        if s.rest and s.rest.n_lux:
            w(f'Resto de OpenRed: {s.rest_n_measurements:,} medidas · mediana {s.rest.lux_median:.1f} lux · '
              f'{s.rest.cct_median:.0f} K · ≤3000 K {s.rest.cct_pct_le_3000}%')
            w(f'  Por tipo de zona, solo lugares con ≥{REST_PLACE_MIN_NIGHT} medidas nocturnas y ≥{REST_PLACE_MIN_TRACKS} tracks:')
            for g in GROUP_ORDER:
                z = s.rest_zones.get(g)
                if z and z.n_lux:
                    w(f'  {GROUP_LABELS_ES[g]:<24} {z.n_lux:>6,} medidas · {z.lux_median:.1f} lux · '
                      + (f'{z.cct_median:.0f} K' if z.n_cct else '-') + f' · {z.n_places} lugares')
            w('  Lugares del resto de OpenRed (celda H3 res 5):')
            for p in s.rest_places:
                if p['n_night'] < 50:
                    continue
                groups = ', '.join(f'{g} {n}' for g, n in sorted(p['groups'].items(), key=lambda x: -x[1]))
                w(f'    {p["lat"]:7.3f},{p["lon"]:8.3f}  {p["n_night"]:>6,} noct. {p["n_tracks"]:>3} tracks  '
                  f'{"OK " if p["qualifies"] else "no "} [{groups}]')
        w('Participantes:')
        for u in sorted(s.users.values(), key=lambda u: -u.n_measurements):
            flag = '' if u.email else '  (SIN EMAIL / inactivo: no se envía)'
            w(f'  {u.username:<16} {u.email:<32} {u.n_measurements:>6,} medidas  {u.n_tracks:>3} tracks  '
              f'{u.km:>6.1f} km  {u.n_nights} noches{flag}')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        if options['action'] == 'fetch-zones':
            path, n = fetch_city_zones(options['city'])
            self.stdout.write(self.style.SUCCESS(f'{n} polígonos guardados en {path}'))
            return
        summary = self._summary(options)
        self._print_stats(summary)
        if summary.n_measurements == 0:
            raise CommandError('No hay medidas en esa zona y periodo')
        return getattr(self, f"handle_{options['action']}")(summary, options)

    def handle_stats(self, summary, o):
        if o['save_html']:
            self._save_html(summary, o, summary.recipients()[0] if summary.recipients() else None)

    def _save_html(self, summary, o, user_stats):
        charts = render_area_summary_charts(summary, lang=o['lang'])
        msg = build_area_summary_email(summary, user_stats, 'preview@example.org', lang=o['lang'], charts=charts)
        html = next(body for body, mime in msg.alternatives if mime == 'text/html')
        with open(o['save_html'], 'w', encoding='utf-8') as fh:
            fh.write(html)
        self.stdout.write(f'HTML guardado en {o["save_html"]}')

    def handle_test(self, summary, o):
        recipients = summary.recipients()
        if o['no_personal']:
            user_stats = None
        elif o['as_user']:
            user_stats = next((u for u in summary.users.values() if u.username == o['as_user']), None)
            if user_stats is None:
                raise CommandError(f'El usuario {o["as_user"]} no tiene medidas en la zona/periodo')
        else:
            user_stats = recipients[0] if recipients else None
        charts = render_area_summary_charts(summary, lang=o['lang'])
        if o['save_html']:
            self._save_html(summary, o, user_stats)
        self.stdout.write(f'Enviando prueba a {o["to_email"]} con el bloque personal de '
                          f'{user_stats.username if user_stats else "(nadie)"}...')
        try:
            build_area_summary_email(summary, user_stats, o['to_email'], lang=o['lang'], charts=charts) \
                .send(fail_silently=False)
        except ValueError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS('Prueba enviada'))

    def handle_send(self, summary, o):
        recipients = summary.recipients()
        if o['only']:
            wanted = {x.strip() for x in o['only'].split(',') if x.strip()}
            recipients = [u for u in recipients if u.username in wanted]
            missing = wanted - {u.username for u in recipients}
            if missing:
                raise CommandError(f'Sin destinatario para: {", ".join(sorted(missing))}')
        if not recipients:
            raise CommandError('No hay destinatarios con email')
        self.stdout.write(f'Se enviarán {len(recipients)} correos ({o["lang"]}): '
                          + ', '.join(u.email for u in recipients))
        if not o['yes']:
            answer = input('¿Enviar? [escribe "si" para confirmar] ')
            if answer.strip().lower() not in ('si', 'sí', 's', 'yes', 'y'):
                self.stdout.write('Cancelado')
                return
        result = send_area_summary(summary, lang=o['lang'], recipients=recipients)
        for err in result['errors']:
            self.stderr.write(err)
        style = self.style.SUCCESS if result['failed'] == 0 else self.style.WARNING
        self.stdout.write(style(f'Enviados {result["sent"]} · fallidos {result["failed"]}'))
