"""
Monthly radiation activity digest, one personalised email per contributor.

For every contributor of the milestone project (radiation only) it computes the
last 30 days of activity (dose-rate stats, kilometres, usual hour, comparison
with the project and with the previous 30 days). People with no measurements
in the window get a short "we miss you" variant with their lifetime totals.

Entry points:
    compute_digest(until=None, days=30)  -> Digest (project + per-user stats)
    digest_recipients(digest, audience)  -> [UserDigest]
    build_digest_email(digest, user, to_email, charts) -> EmailMultiAlternatives
    send_digest(digest, recipients)      -> {'sent', 'failed', 'errors'}

The management command ``activity_digest`` wraps all of this.

Maximum dose rate
-----------------
A single noisy reading can dominate the maximum, so it is only taken among
readings whose relative error is <= MAX_REL_ERROR; if none qualifies the email
omits it and shows the 95th percentile only.
"""
import io
import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

#: Readings with a larger relative error cannot be the reported maximum.
MAX_REL_ERROR = 0.20
#: Readings above this (µSv/h) are sensor artefacts (the raw maximum in the DB is 1000) and are ignored.
PLAUSIBLE_MAX = 5.0
#: A zone (H3 res 6, ~36 km²) needs this many valid readings to be compared with the project.
MIN_USER_ZONE_READINGS = 30
ZONE_H3_RES = 6
AREA_H3_RES = 5
#: Below this many readings in the window a median/percentile is not meaningful.
MIN_READINGS_FOR_STATS = 30
MONTHS_ES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
             'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


@dataclass
class UserDigest:
    user_id: int
    username: str = ''
    email: str = ''
    display_name: str = ''
    # window
    n: int = 0
    recent_n: int = 0                              # readings in the last `days` (decides active vs miss)
    tracks: set = field(default_factory=set)
    km: float = 0.0
    days: Counter = field(default_factory=Counter)  # local date -> readings
    zcells: dict = field(default_factory=dict)      # H3 res 6 -> [sum dose, n] (valid readings only)
    areas: set = field(default_factory=set)         # H3 res 5 cells measured in the window
    areas_first: int = 0                            # cells where this person was the first to measure
    best: dict = None                               # max validated reading {'dose','lat','lon','at'}
    alts: list = field(default_factory=list)        # (altitude, dose) of valid readings
    map_partial: bool = False                       # heat map shows only the main area
    cells: dict = field(default_factory=dict)       # (lat, lon rounded ~11 m) -> [sum dose, n]
    values: list = field(default_factory=list)     # dose rates (µSv/h)
    max_ok: float = None                           # max among low-error readings
    hours: Counter = field(default_factory=Counter)  # local hour -> readings
    # previous window
    prev_n: int = 0
    prev_median: float = None
    # lifetime
    total_n: int = 0
    total_tracks: int = 0
    total_km: float = 0.0
    first_at: datetime = None
    last_at: datetime = None

    @property
    def active(self):
        return self.recent_n > 0

    @property
    def median(self):
        return statistics.median(self.values) if len(self.values) >= MIN_READINGS_FOR_STATS else None

    @property
    def mean(self):
        return statistics.fmean(self.values) if self.values else None

    @property
    def p95(self):
        return _percentile(self.values, 95) if len(self.values) >= MIN_READINGS_FOR_STATS else None

    @property
    def usual_hour(self):
        return self.hours.most_common(1)[0][0] if self.hours else None


@dataclass
class Digest:
    since: datetime
    until: datetime
    tz: str
    project_name: str
    lifetime: bool = False                         # window = whole history (first mailing)
    recent_since: datetime = None                  # start of the last-`days` window
    days: int = 30
    users: dict = field(default_factory=dict)      # user_id -> UserDigest
    n: int = 0
    n_users: int = 0
    n_tracks: int = 0
    km: float = 0.0
    median: float = None
    p95: float = None
    user_medians: list = field(default_factory=list)
    n_recent_users: int = 0
    zone_totals: dict = field(default_factory=dict)  # H3 res 6 -> [sum, n] over everyone
    milestone: dict = None
    recent_n: int = 0
    recent_km: float = 0.0


def _percentile(values, pct):
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _num(value, decimals=0):
    if value is None:
        return ''
    text = f'{value:,.{decimals}f}'
    return text.replace(',', '\u0000').replace('.', ',').replace('\u0000', '.')


def _dose(value):
    """µSv/h with 3 decimals below 1, 2 above (typical values are ~0.05-0.15)."""
    return _num(value, 3 if value is not None and value < 1 else 2)


def _delta(new, old, decimals=0):
    if new is None or old is None:
        return ''
    diff = new - old
    if round(diff, decimals) == 0:
        return ''
    return f'{"+" if diff > 0 else "−"}{_num(abs(diff), decimals)}'


def _date_es(d):
    return f'{d.day} de {MONTHS_ES[d.month - 1]}'


def compute_digest(until=None, days=30, project_name=None, tz='Europe/Madrid', lifetime=False):
    import h3
    """
    Statistics for [until-days, until) plus the previous window and lifetime.
    With ``lifetime`` the stats cover the whole history (first mailing); who is
    "active" is still decided by the last ``days``.
    """
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db.models import Count, Max, Min
    from missions.models import Project
    from .models import RadiationMeasurement, Track

    name = project_name or getattr(settings, 'CONTRIBUTION_MILESTONE_PROJECT', 'Openred')
    project = Project.objects.filter(name=name).first()
    if project is None:
        raise ValueError(f"Project '{name}' not found")
    zone = ZoneInfo(tz)
    until = until or datetime.now(dt_timezone.utc)
    since = until - timedelta(days=days)
    prev_since = since - timedelta(days=days)
    recent_since = since
    digest = Digest(since=since, until=until, tz=tz, project_name=name, lifetime=lifetime,
                    recent_since=recent_since, days=days)

    base = RadiationMeasurement.objects.filter(project=project, user__isnull=False)

    # Lifetime totals (every contributor, even those silent in the window)
    life = (base.values('user_id')
            .annotate(n=Count('id'), first=Min('dateTime'), last=Max('dateTime'), tracks=Count('track_id', distinct=True)))
    for row in life:
        u = digest.users.setdefault(row['user_id'], UserDigest(user_id=row['user_id']))
        u.total_n, u.first_at, u.last_at, u.total_tracks = row['n'], row['first'], row['last'], row['tracks']
    if lifetime:
        digest.since = since = min((u.first_at for u in digest.users.values() if u.first_at), default=since)

    # Window
    track_ids = set()
    recent_tracks = set()
    rows = (base.filter(dateTime__gte=since, dateTime__lt=until, dose_rate__isnull=False, dose_rate__lte=PLAUSIBLE_MAX)
            .values_list('user_id', 'dateTime', 'dose_rate', 'dose_rate_rel_error', 'track_id', 'latitude', 'longitude', 'altitude'))
    all_values = []
    for user_id, dt, dose, rel_err, track_id, lat, lon, alt in rows.iterator(chunk_size=5000):
        u = digest.users[user_id]
        local = dt.astimezone(zone)
        u.n += 1
        if dt >= recent_since:
            u.recent_n += 1
            if track_id:
                recent_tracks.add(track_id)
        u.values.append(dose)
        u.days[local.date()] += 1
        cell = u.cells.setdefault((round(float(lat), 4), round(float(lon), 4)), [0.0, 0])
        cell[0] += dose
        cell[1] += 1
        u.hours[local.hour] += 1
        lat, lon = float(lat), float(lon)
        u.areas.add(h3.latlng_to_cell(lat, lon, AREA_H3_RES))
        if rel_err is not None and rel_err <= MAX_REL_ERROR:
            zone_cell = h3.latlng_to_cell(lat, lon, ZONE_H3_RES)
            for acc in (u.zcells, digest.zone_totals):
                z = acc.setdefault(zone_cell, [0.0, 0])
                z[0] += dose
                z[1] += 1
            if alt is not None:
                u.alts.append((alt, dose))
            if u.max_ok is None or dose > u.max_ok:
                u.max_ok = dose
                u.best = {'dose': dose, 'err': rel_err, 'lat': lat, 'lon': lon, 'at': local}
        if track_id:
            u.tracks.add(track_id)
            track_ids.add(track_id)
        all_values.append(dose)

    # Previous window (median + count only)
    prev = defaultdict(list)
    for user_id, dose in ([] if lifetime else base.filter(dateTime__gte=prev_since, dateTime__lt=since, dose_rate__isnull=False, dose_rate__lte=PLAUSIBLE_MAX)
                          .values_list('user_id', 'dose_rate').iterator(chunk_size=5000)):
        prev[user_id].append(dose)
    for user_id, vals in prev.items():
        u = digest.users[user_id]
        u.prev_n = len(vals)
        u.prev_median = statistics.median(vals) if len(vals) >= MIN_READINGS_FOR_STATS else None

    # Kilometres (a track counts in full if it has readings in the window)
    all_user_tracks = defaultdict(set)
    for user_id, track_id in base.exclude(track__isnull=True).values_list('user_id', 'track_id').distinct():
        all_user_tracks[user_id].add(track_id)
    track_km = {t['id']: (t['total_distance'] or 0.0) / 1000.0
                for t in Track.objects.filter(id__in=track_ids | {t for s in all_user_tracks.values() for t in s})
                .values('id', 'total_distance')}
    for u in digest.users.values():
        u.km = sum(track_km.get(t, 0.0) for t in u.tracks)
        u.total_km = sum(track_km.get(t, 0.0) for t in all_user_tracks.get(u.user_id, ()))

    # Areas (H3 res 5) where each person was the first ever to measure
    first_in_area = {}
    for lat, lon, first_dt, first_user in _first_in_fine_cells(project.id, until):
        cell = h3.latlng_to_cell(float(lat), float(lon), AREA_H3_RES)
        if cell not in first_in_area or first_dt < first_in_area[cell][0]:
            first_in_area[cell] = (first_dt, first_user)
    for _cell, (_dt, first_user) in first_in_area.items():
        if first_user in digest.users:
            digest.users[first_user].areas_first += 1

    # Project-wide
    digest.n = len(all_values)
    digest.median = statistics.median(all_values) if all_values else None
    digest.p95 = _percentile(all_values, 95)
    digest.n_tracks = len(track_ids)
    digest.km = sum(track_km.get(t, 0.0) for t in track_ids)
    digest.user_medians = sorted(u.median for u in digest.users.values() if u.median is not None)
    digest.n_users = sum(1 for u in digest.users.values() if u.n)
    digest.recent_n = sum(u.recent_n for u in digest.users.values())
    digest.recent_km = sum(track_km.get(t, 0.0) for t in recent_tracks)
    digest.n_recent_users = sum(1 for u in digest.users.values() if u.recent_n)

    from .tasks import count_project_contributions
    step = getattr(settings, 'CONTRIBUTION_MILESTONE_STEP', 100000)
    current = count_project_contributions(name)
    digest.milestone = {'current': current, 'next': (current // step + 1) * step}
    digest.milestone['missing'] = digest.milestone['next'] - current

    User = get_user_model()
    for user in User.objects.filter(id__in=digest.users):
        u = digest.users[user.id]
        u.username = user.username
        u.email = (user.email or '').strip() if user.is_active else ''
        u.display_name = (user.first_name or '').strip() or user.username
    return digest


def _first_in_fine_cells(project_id, until):
    """(lat, lon, first reading time, user of that first reading) per ~110 m cell, all history."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute('''SELECT round(latitude::numeric, 3), round(longitude::numeric, 3), min("dateTime"),
                              (array_agg(user_id ORDER BY "dateTime"))[1]
                       FROM measures_radiation_measurement
                       WHERE project_id = %s AND user_id IS NOT NULL AND "dateTime" < %s GROUP BY 1, 2''',
                    [project_id, until])
        return cur.fetchall()


def digest_recipients(digest, audience='recent'):
    """
    ``all``: every contributor ever (first mailing). ``recent``: active in the
    last 2 windows (60 days), the monthly rule. One per email, most active first.
    """
    from django.conf import settings
    excluded = {x.lower() for x in getattr(settings, 'ACTIVITY_DIGEST_EXCLUDE_USERNAMES', [])}
    cutoff = digest.recent_since - timedelta(days=digest.days)
    out, seen = [], set()
    for u in sorted(digest.users.values(), key=lambda x: (-x.n, -x.total_n)):
        if not u.email or u.email.lower() in seen or u.username.lower() in excluded:
            continue
        if audience == 'recent' and not (u.recent_n or u.prev_n):
            continue
        if u.total_n == 0 or (audience == 'recent' and u.last_at and u.last_at < cutoff):
            continue
        seen.add(u.email.lower())
        out.append(u)
    return out


def _fig_png(fig):
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=110, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return buf.getvalue()


def _style(ax, xlabel=None, ylabel=None, title=None):
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(labelsize=8)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9)
    if title:
        ax.set_title(title, fontsize=11, fontweight='bold')


def _uri_png(uri):
    import base64
    return base64.b64decode(uri.split(',', 1)[1]) if uri else None


def _heat_map_png(user):
    """Hexagon heat map of the mean dose rate where this person measured."""
    import h3
    from django.utils import translation
    from .report_generators import bbox_to_resolution, data_extent, generate_h3_map, generate_points_map
    points = [{'lat': lat, 'lon': lon, 'value': tot / n, 'n': n} for (lat, lon), (tot, n) in user.cells.items()]
    if not points:
        return None
    # Frame the area where most readings are: the densest ~25 km H3 cell and its
    # surroundings (~75 km). Far-away trips would otherwise shrink the hexagons to dots.
    cell_n = Counter()
    for p in points:
        cell_n[h3.latlng_to_cell(p['lat'], p['lon'], 4)] += p['n']
    core = set(h3.grid_disk(cell_n.most_common(1)[0][0], 3))
    inside = [p for p in points if h3.latlng_to_cell(p['lat'], p['lon'], 4) in core]
    south, north = min(p['lat'] for p in inside), max(p['lat'] for p in inside)
    west, east = min(p['lon'] for p in inside), max(p['lon'] for p in inside)
    pad_lat, pad_lon = max((north - south) * .1, .02), max((east - west) * .1, .03)
    bbox = {'south': south - pad_lat, 'north': north + pad_lat, 'west': west - pad_lon, 'east': east + pad_lon}
    user.map_partial = len(inside) < len(points)
    points = inside
    res = min(bbox_to_resolution(bbox) + 1, 10)
    acc = defaultdict(lambda: [0.0, 0])
    for pt in points:
        a = acc[h3.latlng_to_cell(pt['lat'], pt['lon'], res)]
        a[0] += pt['value']
        a[1] += 1
    hexes = [{'h3_index': k, 'avg_value': t / n} for k, (t, n) in acc.items()]
    with translation.override('es'):
        if len(hexes) < 4:
            uri = generate_points_map(points, bbox=bbox, value_label='Dosis media (µSv/h)')
        else:
            uri = generate_h3_map(hexes, res, bbox=bbox, value_label='Dosis media (µSv/h)')
    return _uri_png(uri)


def render_user_charts(user, digest):
    """
    {'map', 'dose', 'hours', 'time'} -> PNG bytes or None for one active person:
    heat map of the area, dose-rate histogram (with the project median), usual
    hours and activity over time.
    """
    charts = {'map': None, 'dose': None, 'hours': None, 'time': None}
    if not user.active:
        return charts
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        try:
            charts['map'] = _heat_map_png(user)
        except Exception as e:
            logger.error(f'heat map failed for user {user.user_id}: {e}', exc_info=True)

        if len(user.values) >= MIN_READINGS_FOR_STATS:
            vals = [v for v in user.values if v > 0]
            hi = _percentile(vals, 99.5)
            vals = [v for v in vals if v <= hi]
            fig, ax = plt.subplots(figsize=(8, 3.2))
            ax.hist(vals, bins=np.linspace(0, hi, 41), color='#f59e0b', edgecolor='white', linewidth=0.4)
            if digest.median is not None and digest.median <= hi:
                ax.axvline(digest.median, color='#0f172a', linestyle='--', linewidth=1)
                ax.text(digest.median, ax.get_ylim()[1] * 0.92, ' mediana del proyecto', fontsize=8, color='#0f172a')
            _style(ax, 'Tasa de dosis (µSv/h)', 'Medidas',
                   'Tus medidas: ¿qué dosis has registrado?')
            fig.tight_layout()
            charts['dose'] = _fig_png(fig)

        if user.hours:
            fig, ax = plt.subplots(figsize=(8, 2.8))
            hours = list(range(24))
            counts = [user.hours.get(h, 0) for h in hours]
            top = max(counts)
            ax.bar(hours, counts, color=['#b45309' if c == top else '#fbbf24' for c in counts])
            ax.set_xticks(range(0, 24, 2))
            _style(ax, 'Hora del día (hora de España)', 'Medidas', '¿A qué hora mides?')
            fig.tight_layout()
            charts['hours'] = _fig_png(fig)

        if len(user.days) >= 2:
            fig, ax = plt.subplots(figsize=(8, 2.8))
            if digest.lifetime:
                monthly = Counter()
                for d, c in user.days.items():
                    monthly[(d.year, d.month)] += c
                keys = sorted(monthly)
                ax.bar(range(len(keys)), [monthly[k] for k in keys], color='#f59e0b')
                step = max(1, len(keys) // 8)
                ax.set_xticks(range(0, len(keys), step))
                ax.set_xticklabels([f'{MONTHS_ES[keys[i][1] - 1][:3]} {keys[i][0] % 100}' for i in range(0, len(keys), step)])
                title = 'Tu actividad mes a mes'
            else:
                ds = sorted(user.days)
                ax.bar([(d - ds[0]).days for d in ds], [user.days[d] for d in ds], color='#f59e0b')
                title = 'Tu actividad día a día'
            _style(ax, None, 'Medidas', title)
            fig.tight_layout()
            charts['time'] = _fig_png(fig)
    except Exception as e:
        logger.error(f'render_user_charts failed: {e}', exc_info=True)
    return charts


def logo_png():
    """Radiation trefoil (yellow disc, black blades) for the email header; PNG bytes."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Wedge
    fig, ax = plt.subplots(figsize=(1, 1), dpi=128)
    ax.add_patch(Circle((0, 0), 1, color='#facc15'))
    for start in (30, 150, 270):
        ax.add_patch(Wedge((0, 0), 0.88, start, start + 60, width=0.66, color='#0f172a'))
    ax.add_patch(Circle((0, 0), 0.2, color='#0f172a'))
    ax.set_xlim(-1.02, 1.02)
    ax.set_ylim(-1.02, 1.02)
    ax.set_aspect('equal')
    ax.axis('off')
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=128, transparent=True, bbox_inches='tight', pad_inches=0)
    plt.close(fig)
    return buf.getvalue()


def _highlights(digest, user):
    """Findings of one person: strongest zone vs the project, validated maximum, altitude, territory."""
    from .monthly_report import place_name
    out = {'n_areas': len(user.areas), 'areas_first': user.areas_first,
           'share_pct': _num(100 * user.n / digest.n, 1) if digest.n else '',
           'milestone': ({'next': _num(digest.milestone['next']), 'missing': _num(digest.milestone['missing']),
                          'current': _num(digest.milestone['current']),
                          'pct': _num(100 * digest.milestone['current'] / digest.milestone['next'], 1).replace(',', '.')}
                         if digest.milestone else None)}
    zones = [(cell, tot / n, n) for cell, (tot, n) in user.zcells.items() if n >= MIN_USER_ZONE_READINGS]
    if zones:
        cell, mean, n = max(zones, key=lambda z: z[1])
        import h3
        lat, lon = h3.cell_to_latlng(cell)
        all_sum, all_n = digest.zone_totals.get(cell, [0.0, 0])
        others_n = all_n - n
        others = (all_sum - mean * n) / others_n if others_n >= MIN_USER_ZONE_READINGS else None
        out['top_zone'] = {'name': place_name(lat, lon), 'mean': _dose(mean), 'n': _num(n),
                           'others': _dose(others) if others is not None else '',
                           'x': _num(mean / digest.median, 1) if digest.median else ''}
    if user.best:
        b = user.best
        out['best'] = {'dose': _dose(b['dose']), 'err': _num(b['err'] * 100), 'name': place_name(b['lat'], b['lon']),
                       'date': _date_es(b['at'].date()), 'year': b['at'].year}
    if len(user.alts) >= 100:
        alts = sorted(a for a, _ in user.alts)
        p98 = alts[int(len(alts) * 0.98)]
        high = [d for a, d in user.alts if a >= p98]
        low = [d for a, d in user.alts if a < 300]
        if p98 >= 600 and len(high) >= 20 and len(low) >= MIN_USER_ZONE_READINGS:
            out['altitude'] = {'m': _num(round(p98, -1)), 'high': _dose(statistics.fmean(high)),
                               'low': _dose(statistics.fmean(low))}
    return out


def email_context(digest, user, charts=None):
    from django.conf import settings
    active = user.active
    ctx = {
        'site_url': getattr(settings, 'FRONTEND_URL', 'https://map.open-red.es'),
        'name': user.display_name,
        'active': active,
        'period': f'{_date_es(digest.since.astimezone(ZoneInfo(digest.tz)).date())} – '
                  f'{_date_es(digest.until.astimezone(ZoneInfo(digest.tz)).date())}',
        'charts': {k: bool(v) for k, v in (charts or {}).items()},
        'lifetime': digest.lifetime,
        'map_partial': user.map_partial,
        'recent_n': _num(user.recent_n),
        'since_label': _date_es(digest.since.astimezone(ZoneInfo(digest.tz)).date()) + f' de {digest.since.year}',
        'project_n': _num(digest.n),
        'project_users': digest.n_users,
        'project_km': _num(digest.km, 0),
        'project_median': _dose(digest.median),
        'recent_project_n': _num(digest.recent_n),
        'recent_project_users': digest.n_recent_users,
        'recent_project_km': _num(digest.recent_km, 0),
    }
    if active:
        ctx.update({
            'n': _num(user.n),
            'd_n': _delta(user.n, user.prev_n) if user.prev_n else '',
            'tracks': len(user.tracks),
            'km': _num(user.km, 1),
            'days': len(user.days),
            'median': _dose(user.median) if user.median is not None else '',
            'mean': _dose(user.mean),
            'p95': _dose(user.p95) if user.p95 is not None else '',
            'max': _dose(user.max_ok) if user.max_ok is not None else '',
            'd_median': _delta(user.median, user.prev_median, 3) if user.prev_median is not None else '',
            'prev_median': _dose(user.prev_median) if user.prev_median is not None else '',
            'vs_project': '',
            'usual_hour': f'{user.usual_hour:02d}:00–{(user.usual_hour + 1) % 24:02d}:00' if user.usual_hour is not None else '',
        })
        if user.median is not None and digest.median:
            ratio = user.median / digest.median
            ctx['vs_project'] = ('en línea con' if 0.9 <= ratio <= 1.1 else
                                 f'un {_num(abs(ratio - 1) * 100)} % {"por encima" if ratio > 1 else "por debajo"} de')
        ctx.update(_highlights(digest, user))
    else:
        ctx.update({
            'total_n': _num(user.total_n),
            'total_tracks': user.total_tracks,
            'total_km': _num(user.total_km, 0),
            'last_date': _date_es(user.last_at.astimezone(ZoneInfo(digest.tz)).date()) if user.last_at else '',
            'first_year': user.first_at.year if user.first_at else '',
        })
    return ctx


def build_digest_email(digest, user, to_email, charts=None, connection=None):
    from email.mime.image import MIMEImage
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    ctx = email_context(digest, user, charts=charts)
    kind = 'active' if user.active else 'miss'
    base = f'emails/activity_digest_{kind}'
    message = EmailMultiAlternatives(
        subject=render_to_string(f'{base}_subject.txt', ctx).strip(),
        body=render_to_string(f'{base}.txt', ctx),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=[to_email],
        connection=connection,
    )
    message.attach_alternative(render_to_string(f'{base}.html', ctx), 'text/html')
    message.mixed_subtype = 'related'
    attachments = dict(charts or {})
    attachments['logo'] = logo_png()
    for key, png in attachments.items():
        if png:
            img = MIMEImage(png, _subtype='png')
            img.add_header('Content-ID', f'<chart_{key}>')
            img.add_header('Content-Disposition', 'inline', filename=f'{key}.png')
            message.attach(img)
    message.log_info = {'variant': kind, 'payload': ctx}
    return message


def send_digest(digest, recipients, run=None, period=None):
    """
    Send one email per recipient. With ``period`` every outcome is recorded in EmailLog and anyone
    who already received this period's digest is skipped (so a run can be resumed safely).
    """
    from django.core.mail import get_connection
    from . import email_runs
    done = email_runs.already_sent('activity_digest', period) if period else set()
    sent = failed = skipped = 0
    errors = []
    conn = get_connection()
    conn.open()
    try:
        for u in recipients:
            if u.email.lower() in done:
                skipped += 1
                continue
            try:
                msg = build_digest_email(digest, u, u.email, charts=render_user_charts(u, digest), connection=conn)
                msg.send()
                sent += 1
                if period:
                    email_runs.log_email('activity_digest', period, u.email, 'sent', run=run, user_id=u.user_id,
                                         variant=msg.log_info['variant'], subject=msg.subject, payload=msg.log_info['payload'])
            except Exception as e:
                failed += 1
                errors.append(f'{u.email}: {e}')
                logger.error(f'Activity digest to {u.email} failed: {e}')
                if period:
                    email_runs.log_email('activity_digest', period, u.email, 'failed', run=run, user_id=u.user_id,
                                         error=str(e))
    finally:
        conn.close()
    logger.info(f'Activity digest {digest.since:%Y-%m-%d}..{digest.until:%Y-%m-%d}: sent={sent} failed={failed} skipped={skipped}')
    return {'sent': sent, 'failed': failed, 'skipped': skipped, 'errors': errors}
