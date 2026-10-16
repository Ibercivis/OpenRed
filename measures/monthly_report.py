"""
Monthly OpenRed Radiación report: the global picture of the project (radiation
only), since the beginning and what changed in one calendar month.

Sent around the 2nd of each month for the previous month; the personal digest
(``activity_digest``) goes out around the 16th.

Entry points:
    compute_report(month)          -> Report (all figures; ``month`` = first day)
    render_report_charts(report)   -> {'growth','map','zones','altitude','dist','when'} PNG bytes
    report_recipients()            -> [(user_id, email, display_name)]
    build_report_email(report, to_email, charts) -> EmailMultiAlternatives
    send_report(report, recipients)

Data hygiene
------------
* Readings above PLAUSIBLE_MAX µSv/h are sensor artefacts (the raw maximum in the
  database is 1000 µSv/h) and are excluded from every statistic.
* "Findings" (hot zones, maximum reading) use only readings whose relative error is
  <= MAX_REL_ERROR, and zones need MIN_ZONE_READINGS readings from >= MIN_ZONE_TRACKS
  tracks, so one noisy walk cannot headline the report.
"""
import io
import json
import logging
import os
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from .activity_digest import MAX_REL_ERROR, MONTHS_ES, PLAUSIBLE_MAX, _dose, _fig_png, _num, _percentile, _style, _uri_png, logo_png

logger = logging.getLogger(__name__)

TZ = 'Europe/Madrid'
MIN_ZONE_READINGS = 100
MIN_ZONE_TRACKS = 2
ZONE_H3_RES = 6              # ~36 km² cells for "zones"
COVERAGE_H3_RES = 5          # ~250 km² cells for "new areas covered"
ALTITUDE_BANDS = [(-1e9, 100, '< 100 m'), (100, 300, '100–300'), (300, 600, '300–600'),
                  (600, 1000, '600–1000'), (1000, 1500, '1000–1500'), (1500, 1e9, '> 1500 m')]
WEEKDAYS_ES = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
GEOCACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'geocache.json')


@dataclass
class Report:
    month: date                    # first day of the reported month
    start: datetime                # UTC bounds of the month
    end: datetime
    project_name: str
    first_at: datetime = None
    # cumulative up to ``end``
    total_n: int = 0
    total_users: int = 0
    total_tracks: int = 0
    total_km: float = 0.0
    # month and previous month
    m: dict = field(default_factory=dict)
    prev: dict = field(default_factory=dict)
    monthly: list = field(default_factory=list)      # [(date, n, users, new_users, cum_users)]
    # dose statistics
    all_stats: dict = field(default_factory=dict)    # median/p25/p75/p95/n
    month_stats: dict = field(default_factory=dict)
    n_artefacts: int = 0
    n_noisy: int = 0                                  # readings with rel error > MAX_REL_ERROR (all time)
    # findings
    zones: list = field(default_factory=list)         # top zones by mean dose
    low_zone: dict = None
    max_reading: dict = None
    longest_km: float = 0.0
    altitude: list = field(default_factory=list)
    # coverage and network
    areas_total: int = 0
    areas_new: int = 0
    devices_total: int = 0
    devices_month: int = 0
    models: list = field(default_factory=list)        # [(name, n, pct)]
    # chart inputs
    hexes: list = field(default_factory=list)
    map_bbox: dict = None
    map_res: int = 4
    values_all: list = field(default_factory=list)
    values_month: list = field(default_factory=list)
    when: list = field(default_factory=list)          # 7x24 counts (month, local time)
    # milestone
    milestone: dict = None


def month_bounds(month, tz=TZ):
    zone = ZoneInfo(tz)
    start = datetime(month.year, month.month, 1, tzinfo=zone)
    nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return start.astimezone(dt_timezone.utc), datetime(nxt.year, nxt.month, 1, tzinfo=zone).astimezone(dt_timezone.utc)


def previous_month(today=None):
    today = today or datetime.now(ZoneInfo(TZ)).date()
    first = today.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def _query(sql, params=()):
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _stats(values):
    if not values:
        return {'n': 0}
    return {'n': len(values), 'median': statistics.median(values), 'p25': _percentile(values, 25),
            'p75': _percentile(values, 75), 'p95': _percentile(values, 95), 'mean': statistics.fmean(values)}


# --------------------------------------------------------------------------
# Place names (Nominatim, cached on disk)
# --------------------------------------------------------------------------
def place_name(lat, lon):
    """'Municipio (País)' for a coordinate; falls back to the coordinates offline."""
    key = f'{lat:.2f},{lon:.2f}'
    try:
        with open(GEOCACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        return cache[key]
    name = f'{abs(lat):.2f}°{"N" if lat >= 0 else "S"}, {abs(lon):.2f}°{"E" if lon >= 0 else "O"}'
    try:
        from geopy.geocoders import Nominatim
        geo = Nominatim(user_agent='OpenRed-API/1.0 (+https://api.open-red.es; noreply@ibercivis.es)', timeout=10)
        loc = geo.reverse((lat, lon), zoom=10, language='es')
        addr = (loc.raw.get('address') if loc else None) or {}
        town = (addr.get('city') or addr.get('town') or addr.get('village') or addr.get('municipality')
                or addr.get('county') or addr.get('state'))
        if town:
            name = f'{town} ({addr["country"]})' if addr.get('country') else town
        time.sleep(1.1)   # Nominatim usage policy: max 1 request per second
        cache[key] = name
        os.makedirs(os.path.dirname(GEOCACHE), exist_ok=True)
        with open(GEOCACHE, 'w') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.warning(f'Reverse geocoding failed for {key}: {e}')
    return name


# --------------------------------------------------------------------------
# Computation
# --------------------------------------------------------------------------
def compute_report(month, project_name=None, geocode=True):
    import h3
    from django.conf import settings
    from missions.models import Project
    from .models import RadiationMeasurement, Track
    from .report_generators import bbox_to_resolution

    name = project_name or getattr(settings, 'CONTRIBUTION_MILESTONE_PROJECT', 'Openred')
    project = Project.objects.filter(name=name).first()
    if project is None:
        raise ValueError(f"Project '{name}' not found")
    start, end = month_bounds(month)
    r = Report(month=month, start=start, end=end, project_name=name)
    zone = ZoneInfo(TZ)
    pid = project.id
    ok = 'project_id = %s AND user_id IS NOT NULL AND dose_rate IS NOT NULL AND "dateTime" < %s'

    # --- monthly series, users first seen
    series = _query(f'''SELECT date_trunc('month', "dateTime" AT TIME ZONE 'Europe/Madrid')::date, count(*), count(DISTINCT user_id)
                        FROM measures_radiation_measurement WHERE {ok} AND dose_rate <= %s GROUP BY 1 ORDER BY 1''',
                    (pid, end, PLAUSIBLE_MAX))
    first_seen = dict(_query(f'''SELECT user_id, min("dateTime") FROM measures_radiation_measurement
                                 WHERE {ok} GROUP BY 1''', (pid, end)))
    new_by_month = Counter(date(d.astimezone(zone).year, d.astimezone(zone).month, 1) for d in first_seen.values())
    cum = 0
    for m_date, n, users in series:
        cum += new_by_month.get(m_date, 0)
        r.monthly.append((m_date, n, users, new_by_month.get(m_date, 0), cum))
    r.first_at = min(first_seen.values()) if first_seen else None
    r.total_n = sum(row[1] for row in series)
    r.total_users = len(first_seen)

    # --- tracks / km
    def track_stats(a, b):
        qs = Track.objects.filter(project_id=pid, track_type='radiation', start_time__gte=a, start_time__lt=b)
        km = [(t or 0) / 1000.0 for t in qs.values_list('total_distance', flat=True)]
        return len(km), sum(km), max(km, default=0.0)
    r.total_tracks, r.total_km, _ = track_stats(datetime(2000, 1, 1, tzinfo=dt_timezone.utc), end)
    pstart, _ = month_bounds(date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1))
    t_n, t_km, r.longest_km = track_stats(start, end)
    p_n, p_km, _ = track_stats(pstart, start)

    row_m = next((s for s in r.monthly if s[0] == month), (month, 0, 0, 0, r.total_users))
    prev_month = date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)
    row_p = next((s for s in r.monthly if s[0] == prev_month), (prev_month, 0, 0, 0, 0))
    r.m = {'n': row_m[1], 'users': row_m[2], 'new_users': row_m[3], 'tracks': t_n, 'km': t_km}
    r.prev = {'n': row_p[1], 'users': row_p[2], 'tracks': p_n, 'km': p_km}

    # --- artefacts / noise
    r.n_artefacts, r.n_noisy = _query(f'''SELECT count(*) FILTER (WHERE dose_rate > %s),
                                                 count(*) FILTER (WHERE dose_rate <= %s AND dose_rate_rel_error > %s)
                                          FROM measures_radiation_measurement WHERE {ok}''',
                                      (PLAUSIBLE_MAX, PLAUSIBLE_MAX, MAX_REL_ERROR, pid, end))[0]

    # --- all-time dose values (for the distribution and statistics)
    r.values_all = [v for (v,) in _query(f'''SELECT dose_rate FROM measures_radiation_measurement
                                             WHERE {ok} AND dose_rate > 0 AND dose_rate <= %s''', (pid, end, PLAUSIBLE_MAX))]
    r.all_stats = _stats(r.values_all)

    # --- month rows (python): zones, max reading, when, month statistics
    rows = _query(f'''SELECT "dateTime", latitude, longitude, altitude, dose_rate, dose_rate_rel_error, track_id, user_id, device_id
                      FROM measures_radiation_measurement WHERE {ok} AND "dateTime" >= %s AND dose_rate > 0 AND dose_rate <= %s''',
                  (pid, end, start, PLAUSIBLE_MAX))
    r.values_month = [row[4] for row in rows]
    r.month_stats = _stats(r.values_month)
    r.devices_month = len({row[8] for row in rows})
    r.when = [[0] * 24 for _ in range(7)]
    cells = defaultdict(lambda: {'vals': [], 'users': set(), 'tracks': set()})
    best = None
    for dt, lat, lon, alt, dose, err, track_id, user_id, device_id in rows:
        local = dt.astimezone(zone)
        r.when[local.weekday()][local.hour] += 1
        if err is None or err > MAX_REL_ERROR:
            continue
        lat, lon = float(lat), float(lon)
        c = cells[h3.latlng_to_cell(lat, lon, ZONE_H3_RES)]
        c['vals'].append(dose)
        c['users'].add(user_id)
        if track_id:
            c['tracks'].add(track_id)
        if best is None or dose > best['dose']:
            best = {'dose': dose, 'err': err, 'lat': lat, 'lon': lon, 'at': local}
    qualifying = [{'cell': k, 'n': len(v['vals']), 'users': len(v['users']), 'tracks': len(v['tracks']),
                   'mean': statistics.fmean(v['vals'])}
                  for k, v in cells.items() if len(v['vals']) >= MIN_ZONE_READINGS and len(v['tracks']) >= MIN_ZONE_TRACKS]
    qualifying.sort(key=lambda z: -z['mean'])
    # Name the candidates and merge neighbouring cells that fall in the same place.
    merged = {}
    for z in qualifying[:16] + qualifying[-3:]:
        z['lat'], z['lon'] = h3.cell_to_latlng(z['cell'])
        z['name'] = place_name(z['lat'], z['lon']) if geocode else f"{z['lat']:.2f}, {z['lon']:.2f}"
        m = merged.get(z['name'])
        if m is None:
            merged[z['name']] = dict(z)
        elif m['cell'] != z['cell']:
            total = m['n'] + z['n']
            m['mean'] = (m['mean'] * m['n'] + z['mean'] * z['n']) / total
            m.update(n=total, tracks=max(m['tracks'], z['tracks']), users=max(m['users'], z['users']))
    ranked = sorted(merged.values(), key=lambda z: -z['mean'])
    r.zones = ranked[:8]
    r.low_zone = ranked[-1] if len(ranked) > 8 else None
    if best:
        best['name'] = place_name(best['lat'], best['lon']) if geocode else ''
        r.max_reading = best

    # --- altitude (all time, month)
    def band_stats(rows_iter):
        out = defaultdict(list)
        for alt, dose in rows_iter:
            if alt is None:
                continue
            for lo, hi, label in ALTITUDE_BANDS:
                if lo <= alt < hi:
                    out[label].append(dose)
                    break
        return out
    all_alt = band_stats(_query(f'''SELECT altitude, dose_rate FROM measures_radiation_measurement
                                    WHERE {ok} AND dose_rate > 0 AND dose_rate <= %s AND altitude IS NOT NULL''',
                                (pid, end, PLAUSIBLE_MAX)))
    month_alt = band_stats((row[3], row[4]) for row in rows)
    r.altitude = [{'label': label, 'n': len(all_alt[label]), 'median': statistics.median(all_alt[label]),
                   'n_month': len(month_alt[label]),
                   'median_month': statistics.median(month_alt[label]) if len(month_alt[label]) >= 30 else None}
                  for _, _, label in ALTITUDE_BANDS if len(all_alt[label]) >= 100]

    # --- coverage: fine cells with first-seen date -> H3 res 5 areas, hexagons for the map
    fine = _query(f'''SELECT round(latitude::numeric, 3), round(longitude::numeric, 3), avg(dose_rate), count(*), min("dateTime")
                      FROM measures_radiation_measurement WHERE {ok} AND dose_rate > 0 AND dose_rate <= %s GROUP BY 1, 2''',
                  (pid, end, PLAUSIBLE_MAX))
    area_first = {}
    for lat, lon, avg, n, first in fine:
        a = h3.latlng_to_cell(float(lat), float(lon), COVERAGE_H3_RES)
        if a not in area_first or first < area_first[a]:
            area_first[a] = first
    r.areas_total = len(area_first)
    r.areas_new = sum(1 for f in area_first.values() if f >= start)
    total_w = sum(f[3] for f in fine)

    def wpct(idx, q):
        acc = 0
        for f in sorted(fine, key=lambda f: f[idx]):
            acc += f[3]
            if acc >= total_w * q:
                return float(f[idx])
    if fine:
        r.map_bbox = {'south': wpct(0, .02), 'north': wpct(0, .98), 'west': wpct(1, .02), 'east': wpct(1, .98)}
        pad = max(r.map_bbox['north'] - r.map_bbox['south'], r.map_bbox['east'] - r.map_bbox['west']) * .08 + .02
        r.map_bbox = {'south': r.map_bbox['south'] - pad, 'north': r.map_bbox['north'] + pad,
                      'west': r.map_bbox['west'] - pad, 'east': r.map_bbox['east'] + pad}
        r.map_res = min(bbox_to_resolution(r.map_bbox) + 1, 7)
        acc = defaultdict(lambda: [0.0, 0])
        for lat, lon, avg, n, _first in fine:
            lat, lon = float(lat), float(lon)
            if r.map_bbox['south'] <= lat <= r.map_bbox['north'] and r.map_bbox['west'] <= lon <= r.map_bbox['east']:
                a = acc[h3.latlng_to_cell(lat, lon, r.map_res)]
                a[0] += avg * n
                a[1] += n
        r.hexes = [{'h3_index': k, 'avg_value': t / n, 'n': n} for k, (t, n) in acc.items()]

    # --- devices
    r.devices_total = _query(f'SELECT count(DISTINCT device_id) FROM measures_radiation_measurement WHERE {ok}', (pid, end))[0][0]
    models = _query(f'''SELECT dm.manufacturer || ' ' || dm.name, count(*) FROM measures_radiation_measurement m
                        JOIN devices_device d ON d.id = m.device_id JOIN devices_devicemodel dm ON dm.id = d.device_model_id
                        WHERE m.project_id = %s AND m.user_id IS NOT NULL AND m.dose_rate IS NOT NULL AND m."dateTime" < %s
                        GROUP BY 1 ORDER BY 2 DESC''', (pid, end))
    tot = sum(n for _, n in models) or 1
    r.models = [(n, c, 100 * c / tot) for n, c in models]

    # --- milestone (the official one: contributions of the project, as of now)
    from .tasks import count_project_contributions
    step = getattr(settings, 'CONTRIBUTION_MILESTONE_STEP', 100000)
    current = count_project_contributions(name)
    nxt = (current // step + 1) * step
    recent = [s[1] for s in r.monthly[-4:-1]] or [r.m['n']]
    rate = statistics.fmean(recent) or 1
    r.milestone = {'current': current, 'next': nxt, 'missing': nxt - current, 'pct': 100 * current / nxt,
                   'rate': rate, 'months': (nxt - current) / rate, 'crossed': None}
    # Milestone crossed during the reported month (by upload date, which is what the counter counts)
    from .models import LightPollutionMeasurement
    def uploaded_before(moment):
        return (RadiationMeasurement.objects.filter(project=project, created_at__lt=moment).count()
                + LightPollutionMeasurement.objects.filter(project=project, created_at__lt=moment).count())
    before, after = uploaded_before(start), uploaded_before(end)
    first = getattr(settings, 'CONTRIBUTION_MILESTONE_START', 300000)
    crossed = [t for t in range(first, after + 1, step) if before < t <= after]
    r.milestone['crossed'] = crossed[-1] if crossed else None
    return r


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
AMBER, DARK, SLATE, GREEN = '#f59e0b', '#0f172a', '#94a3b8', '#15803d'


def render_report_charts(r):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from django.utils import translation
    from .report_generators import generate_h3_map

    charts = {}

    def guard(key, fn):
        try:
            charts[key] = fn()
        except Exception as e:
            logger.error(f'report chart {key} failed: {e}', exc_info=True)
            charts[key] = None

    def growth():
        fig, ax = plt.subplots(figsize=(8, 3.4))
        xs = list(range(len(r.monthly)))
        ax.bar(xs, [s[1] for s in r.monthly], color=[DARK if s[0] == r.month else AMBER for s in r.monthly])
        ax.set_xticks(xs)
        ax.set_xticklabels([f'{MONTHS_ES[s[0].month - 1][:3]} {s[0].year % 100}' for s in r.monthly], fontsize=7, rotation=45)
        _style(ax, None, 'Medidas al mes', 'Crecimiento del proyecto')
        ax2 = ax.twinx()
        ax2.plot(xs, [s[4] for s in r.monthly], color=GREEN, marker='o', markersize=3, linewidth=1.6)
        ax2.set_ylabel('Personas acumuladas', fontsize=9, color=GREEN)
        ax2.tick_params(labelsize=8, colors=GREEN)
        ax2.spines['top'].set_visible(False)
        fig.tight_layout()
        return _fig_png(fig)

    def heat_map():
        if not r.hexes:
            return None
        vals = [h['avg_value'] for h in r.hexes]
        cap = _percentile(vals, 98)
        hexes = [dict(h, avg_value=min(h['avg_value'], cap)) for h in r.hexes]
        with translation.override('es'):
            return _uri_png(generate_h3_map(hexes, r.map_res, bbox=r.map_bbox, value_label='Dosis media (µSv/h)'))

    def zones():
        if not r.zones:
            return None
        zs = list(reversed(r.zones))
        fig, ax = plt.subplots(figsize=(8, 0.42 * len(zs) + 1.2))
        ax.barh(range(len(zs)), [z['mean'] for z in zs], color=AMBER)
        ax.set_yticks(range(len(zs)))
        ax.set_yticklabels([z['name'] for z in zs], fontsize=8)
        for i, z in enumerate(zs):
            ax.text(z['mean'], i, f"  {_dose(z['mean'])}  ({_num(z['n'])} lecturas)", va='center', fontsize=7.5, color=DARK)
        med = r.all_stats.get('median')
        if med:
            ax.axvline(med, color=DARK, linestyle='--', linewidth=1)
            ax.text(med, len(zs) - 0.35, ' mediana histórica', fontsize=7.5, color=DARK)
        ax.set_xlim(0, max(z['mean'] for z in zs) * 1.45)
        _style(ax, 'Dosis media (µSv/h)', None, 'Zonas con mayor dosis media del mes')
        fig.tight_layout()
        return _fig_png(fig)

    def altitude():
        if len(r.altitude) < 3:
            return None
        fig, ax = plt.subplots(figsize=(8, 3.2))
        xs = range(len(r.altitude))
        ax.bar(xs, [a['median'] for a in r.altitude], color=AMBER, label='Histórico')
        pts = [(i, a['median_month']) for i, a in enumerate(r.altitude) if a['median_month'] is not None]
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], 'o', color=DARK, label='Este mes')
        ax.set_xticks(list(xs))
        ax.set_xticklabels([f"{a['label']}\n({_num(a['n'])})" for a in r.altitude], fontsize=7.5)
        ax.legend(fontsize=8, frameon=False)
        _style(ax, 'Altitud GPS (m) · entre paréntesis, nº de lecturas', 'Dosis mediana (µSv/h)', 'La dosis sube con la altitud')
        fig.tight_layout()
        return _fig_png(fig)

    def dist():
        if not r.values_all:
            return None
        fig, ax = plt.subplots(figsize=(8, 3.2))
        bins = np.logspace(np.log10(0.01), np.log10(1.0), 50)
        ax.hist(np.clip(r.values_all, 0.01, 1.0), bins=bins, color=AMBER, alpha=.75, density=True, label='Histórico')
        if len(r.values_month) > 100:
            ax.hist(np.clip(r.values_month, 0.01, 1.0), bins=bins, histtype='step', color=DARK, linewidth=1.6,
                    density=True, label='Este mes')
        ax.set_xscale('log')
        for q, label in ((r.all_stats['median'], 'mediana'),):
            ax.axvline(q, color=SLATE, linestyle='--', linewidth=1)
            ax.text(q, ax.get_ylim()[1] * .92, f' {label} {q:.3f}', fontsize=7.5, color=DARK)
        ax.legend(fontsize=8, frameon=False)
        ax.set_yticks([])
        _style(ax, 'Tasa de dosis (µSv/h, escala logarítmica)', None, 'Distribución de todas las medidas')
        fig.tight_layout()
        return _fig_png(fig)

    def when():
        if sum(map(sum, r.when)) < 100:
            return None
        fig, ax = plt.subplots(figsize=(8, 3))
        im = ax.imshow(np.array(r.when), aspect='auto', cmap='YlOrBr')
        ax.set_yticks(range(7))
        ax.set_yticklabels(WEEKDAYS_ES, fontsize=8)
        ax.set_xticks(range(0, 24, 2))
        ax.set_xticklabels([f'{h}h' for h in range(0, 24, 2)], fontsize=8)
        ax.set_title('¿Cuándo medimos? (hora de España)', fontsize=11, fontweight='bold')
        fig.colorbar(im, ax=ax, shrink=.8, pad=.02).ax.tick_params(labelsize=7)
        fig.tight_layout()
        return _fig_png(fig)

    for key, fn in (('growth', growth), ('map', heat_map), ('zones', zones), ('altitude', altitude),
                    ('dist', dist), ('when', when)):
        guard(key, fn)
    return charts


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
def _pct_change(new, old):
    if not old:
        return ''
    p = (new - old) / old * 100
    return f'{"+" if p > 0 else "−"}{_num(abs(p))} %' if round(p) else '0 %'


def _plus(value, decimals=0):
    return f'+{_num(value, decimals)}' if value else ''


def report_context(r, charts=None):
    from django.conf import settings
    m, p = r.m, r.prev
    zone = ZoneInfo(TZ)
    ms, als = r.month_stats, r.all_stats
    ctx = {
        'site_url': getattr(settings, 'FRONTEND_URL', 'https://map.open-red.es'),
        'charts': {k: bool(v) for k, v in (charts or {}).items()},
        'month_label': f'{MONTHS_ES[r.month.month - 1]} de {r.month.year}',
        'month_name': MONTHS_ES[r.month.month - 1],
        'since_label': (f'{r.first_at.astimezone(zone).day} de {MONTHS_ES[r.first_at.astimezone(zone).month - 1]} de '
                        f'{r.first_at.astimezone(zone).year}') if r.first_at else '',
        'total_n': _num(r.total_n), 'total_users': r.total_users, 'total_tracks': _num(r.total_tracks),
        'total_km': _num(r.total_km),
        'd_total_n': _plus(m['n']), 'd_total_users': _plus(m['new_users']), 'd_total_tracks': _plus(m['tracks']),
        'd_total_km': _plus(m['km']),
        'm_n': _num(m['n']), 'm_users': m['users'], 'm_new_users': m['new_users'],
        'm_returning': m['users'] - m['new_users'], 'm_tracks': m['tracks'], 'm_km': _num(m['km']),
        'p_n': _num(p['n']), 'p_users': p['users'], 'p_tracks': p['tracks'], 'p_km': _num(p['km']),
        'v_n': _pct_change(m['n'], p['n']), 'v_users': _pct_change(m['users'], p['users']),
        'v_tracks': _pct_change(m['tracks'], p['tracks']), 'v_km': _pct_change(m['km'], p['km']),
        'areas_total': _num(r.areas_total), 'areas_new': r.areas_new,
        'devices_total': r.devices_total, 'devices_month': r.devices_month,
        'models': [{'name': n, 'n': _num(c), 'pct': _num(pc, 1 if pc < 10 else 0)} for n, c, pc in r.models[:4]],
        'longest_km': _num(r.longest_km, 1),
        'all_median': _dose(als.get('median')), 'all_p25': _dose(als.get('p25')), 'all_p75': _dose(als.get('p75')),
        'all_p95': _dose(als.get('p95')),
        'has_month_stats': ms.get('n', 0) >= 100,
        'month_median': _dose(ms.get('median')) if ms.get('n') else '', 'month_p95': _dose(ms.get('p95')) if ms.get('n') else '',
        'month_mean': _dose(ms.get('mean')) if ms.get('n') else '',
        'd_median': _plus(((ms.get('median') or 0) - als['median']), 3) if ms.get('n', 0) >= 100 and
                    abs(ms['median'] - als['median']) >= 0.0005 else '',
        'zones': [{'rank': i + 1, 'name': z['name'], 'mean': _dose(z['mean']), 'n': _num(z['n']), 'users': z['users'],
                   'tracks': z['tracks'], 'x': _num(z['mean'] / als['median'], 1)} for i, z in enumerate(r.zones)],
        'low_zone': ({'name': r.low_zone['name'], 'mean': _dose(r.low_zone['mean']), 'n': _num(r.low_zone['n'])}
                     if r.low_zone else None),
        'max': ({'dose': _dose(r.max_reading['dose']), 'err': _num(r.max_reading['err'] * 100), 'name': r.max_reading['name'],
                 'date': f"{r.max_reading['at'].day} de {MONTHS_ES[r.max_reading['at'].month - 1]}",
                 'x': _num(r.max_reading['dose'] / als['median'])} if r.max_reading else None),
        'altitude': [{'label': a['label'], 'median': _dose(a['median']), 'n': _num(a['n'])} for a in r.altitude],
        'alt_ratio': _num(r.altitude[-1]['median'] / r.altitude[0]['median'], 1) if len(r.altitude) >= 3 and r.altitude[0]['median'] else '',
        'alt_hi': r.altitude[-1]['label'] if r.altitude else '', 'alt_lo': r.altitude[0]['label'] if r.altitude else '',
        'n_artefacts': _num(r.n_artefacts), 'noisy_pct': _num(100 * r.n_noisy / max(r.total_n, 1)),
        'zone_min_readings': MIN_ZONE_READINGS, 'zone_min_tracks': MIN_ZONE_TRACKS, 'max_err_pct': _num(MAX_REL_ERROR * 100),
        'plausible_max': _num(PLAUSIBLE_MAX),
        'milestone': {'next': _num(r.milestone['next']), 'current': _num(r.milestone['current']),
                      'missing': _num(r.milestone['missing']), 'pct': _num(r.milestone['pct'], 1).replace(',', '.'),
                      'months': _num(r.milestone['months'], 1), 'rate': _num(r.milestone['rate']),
                      'crossed': _num(r.milestone['crossed']) if r.milestone['crossed'] else ''} if r.milestone else None,
    }
    if r.when and sum(map(sum, r.when)):
        flat = [(r.when[d][h], d, h) for d in range(7) for h in range(24)]
        best = max(flat)
        ctx['busiest'] = f'{WEEKDAYS_ES[best[1]]} a las {best[2]}h'
        wd = [sum(r.when[d]) for d in range(7)]
        ctx['weekend_pct'] = _num(100 * (wd[5] + wd[6]) / sum(wd))
    return ctx


def build_report_email(r, to_email, charts=None, connection=None, ctx=None):
    from email.mime.image import MIMEImage
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    ctx = ctx or report_context(r, charts)
    message = EmailMultiAlternatives(
        subject=render_to_string('emails/monthly_report_subject.txt', ctx).strip(),
        body=render_to_string('emails/monthly_report.txt', ctx),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=[to_email], connection=connection,
    )
    message.attach_alternative(render_to_string('emails/monthly_report.html', ctx), 'text/html')
    message.mixed_subtype = 'related'
    attachments = dict(charts or {})
    attachments['logo'] = logo_png()
    for key, png in attachments.items():
        if png:
            img = MIMEImage(png, _subtype='png')
            img.add_header('Content-ID', f'<chart_{key}>')
            img.add_header('Content-Disposition', 'inline', filename=f'{key}.png')
            message.attach(img)
    message.log_info = {'variant': 'report', 'payload': ctx}
    return message


def report_recipients():
    """Every active contributor with an email (one per address), minus the excluded usernames."""
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from missions.models import Project
    from .models import RadiationMeasurement

    project = Project.objects.filter(name=getattr(settings, 'CONTRIBUTION_MILESTONE_PROJECT', 'Openred')).first()
    if project is None:
        return []
    excluded = {x.lower() for x in getattr(settings, 'ACTIVITY_DIGEST_EXCLUDE_USERNAMES', [])}
    ids = set(RadiationMeasurement.objects.filter(project=project, user__isnull=False)
              .values_list('user_id', flat=True).distinct())
    out, seen = [], set()
    for u in get_user_model().objects.filter(id__in=ids, is_active=True).exclude(email='').order_by('id'):
        if u.username.lower() in excluded or u.email.lower() in seen:
            continue
        seen.add(u.email.lower())
        out.append((u.id, u.email.strip(), (u.first_name or '').strip() or u.username))
    return out


def send_report(r, recipients, charts=None, run=None, period=None):
    """Send the report to every (user_id, email, name); with ``period`` outcomes are logged and repeats skipped."""
    from django.core.mail import get_connection
    from . import email_runs
    charts = charts if charts is not None else render_report_charts(r)
    ctx = report_context(r, charts)
    done = email_runs.already_sent('monthly_report', period) if period else set()
    sent = failed = skipped = 0
    errors = []
    conn = get_connection()
    conn.open()
    try:
        for uid, email, _name in recipients:
            if email.lower() in done:
                skipped += 1
                continue
            try:
                msg = build_report_email(r, email, charts=charts, connection=conn, ctx=ctx)
                msg.send()
                sent += 1
                if period:
                    email_runs.log_email('monthly_report', period, email, 'sent', run=run, user_id=uid,
                                         variant='report', subject=msg.subject, payload=ctx)
            except Exception as e:
                failed += 1
                errors.append(f'{email}: {e}')
                logger.error(f'Monthly report to {email} failed: {e}')
                if period:
                    email_runs.log_email('monthly_report', period, email, 'failed', run=run, user_id=uid, error=str(e))
    finally:
        conn.close()
    logger.info(f'Monthly report {r.month:%Y-%m}: sent={sent} failed={failed} skipped={skipped}')
    return {'sent': sent, 'failed': failed, 'skipped': skipped, 'errors': errors}
