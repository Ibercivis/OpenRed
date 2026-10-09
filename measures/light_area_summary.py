"""
Light-pollution thank-you summary for the contributors of an area and period.

Computes the statistics (participants, measurements, kilometres, lux and CCT
distributions, weather, comparison with the rest of OpenRed) for every light
pollution measurement taken within ``radius_km`` of a centre between two
dates, renders two charts and builds one personalised email per contributor.

Entry points:
    compute_area_summary(...)   -> AreaSummary (stats + per-user stats)
    render_area_summary_charts  -> {'lux': png bytes, 'cct': png bytes}
    build_area_summary_email    -> EmailMultiAlternatives for one recipient

The management command ``light_area_summary`` wraps all of this.

Night filter
------------
The light sensor measures horizontal illuminance at ground level. Readings
taken before the end of civil twilight (sun higher than -6 degrees) still
contain daylight and inflate the lux statistics, so every lux/CCT statistic
is computed on "night" readings only; counts of measurements, tracks and
kilometres use everything.
"""
import base64
import json
import logging
import math
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db import connection

from .urban_class import GROUP_LABELS_ES, GROUP_ORDER, group_of

logger = logging.getLogger(__name__)

#: A place elsewhere in OpenRed enters the like-with-like comparison only with
#: this many night readings and tracks (one person's single walk is anecdote).
REST_PLACE_MIN_NIGHT = 300
REST_PLACE_MIN_TRACKS = 2
#: H3 resolution used to group the rest of OpenRed into "places" (~250 km² cells).
REST_PLACE_H3_RES = 5

#: Known centres for ``--city``. Add more as needed (lat, lon, display name).
CITIES = {
    'zaragoza': (41.6488, -0.8891, 'Zaragoza'),
    'huesca': (42.1401, -0.4089, 'Huesca'),
    'teruel': (40.3456, -1.1065, 'Teruel'),
    'madrid': (40.4168, -3.7038, 'Madrid'),
    'barcelona': (41.3874, 2.1686, 'Barcelona'),
    'valencia': (39.4699, -0.3763, 'Valencia'),
    'sevilla': (37.3891, -5.9845, 'Sevilla'),
    'bilbao': (43.2630, -2.9350, 'Bilbao'),
    'pamplona': (42.8125, -1.6458, 'Pamplona'),
    'logrono': (42.4627, -2.4449, 'Logroño'),
    'valladolid': (41.6523, -4.7245, 'Valladolid'),
    'malaga': (36.7213, -4.4214, 'Málaga'),
}

#: Neighbourhood polygon files shipped with the code (GeoJSON, WGS84, property
#: ``name``). Picked automatically for ``--city``; any other GeoJSON works via --zones.
ZONES_DIR = os.path.join(os.path.dirname(__file__), 'data', 'zones')
CITY_ZONES = {
    'zaragoza': 'zaragoza_juntas.geojson',
}
#: Official sources ``fetch_city_zones`` knows how to download and clean.
CITY_ZONES_SOURCES = {
    'zaragoza': {
        'url': 'https://www.zaragoza.es/sede/servicio/distrito.geojson?srsname=wgs84',
        'name_field': 'title',
        'strip_prefixes': ('Junta Municipal ', 'Junta Vecinal '),
        'attribution': 'Ayuntamiento de Zaragoza, datos abiertos, licencia CC BY 4.0',
    },
}
#: A neighbourhood appears in the email only with this many night readings.
NEIGHBOURHOOD_MIN_NIGHT = 100

#: Sun elevation (degrees) below which a reading counts as "night".
#: -6 = end of civil twilight.
DEFAULT_TWILIGHT_ELEVATION = -6.0

#: CCT readings outside this range are sensor noise (the sensor reports
#: millions of K when it saturates or sees almost no light).
CCT_VALID_RANGE = (1000.0, 10000.0)

#: UNE-EN 13201-2 / RD 1890/2008 ITC-EA-02 pedestrian classes (average lux).
#: Used to bucket readings for the email; the reference values are the
#: *average* maintained illuminance each class requires.
LUX_BANDS = [
    ('< 2 lux', 0.0, 2.0),
    ('2 – 5 lux', 2.0, 5.0),
    ('5 – 10 lux', 5.0, 10.0),
    ('10 – 15 lux', 10.0, 15.0),
    ('15 – 30 lux', 15.0, 30.0),
    ('> 30 lux', 30.0, float('inf')),
]

#: Distance rings from the centre (km) used to split "old town / city / outskirts".
DEFAULT_RINGS = (2.0, 6.0)
RING_LABELS_ES = ('Centro (hasta {a} km)', 'Ciudad ({a}–{b} km)', 'Exterior (más de {b} km)')

CCT_BANDS = [
    ('≤ 2.700 K', 0.0, 2700.0),
    ('2.700 – 3.000 K', 2700.0, 3000.0),
    ('3.000 – 4.000 K', 3000.0, 4000.0),
    ('> 4.000 K', 4000.0, float('inf')),
]


# --------------------------------------------------------------------------
# Solar elevation (NOAA low-precision algorithm, good to ~0.1 degree)
# --------------------------------------------------------------------------
def solar_elevation(lat, lon, when_utc):
    """
    Sun elevation above the horizon in degrees at (lat, lon) and ``when_utc``
    (timezone-aware datetime). Positive = day, negative = night.
    """
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=dt_timezone.utc)
    when_utc = when_utc.astimezone(dt_timezone.utc)
    # Days since J2000.0
    jd = (when_utc - datetime(2000, 1, 1, 12, tzinfo=dt_timezone.utc)).total_seconds() / 86400.0
    g = math.radians((357.529 + 0.98560028 * jd) % 360)        # mean anomaly
    q = (280.459 + 0.98564736 * jd) % 360                        # mean longitude
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    eps = math.radians(23.439 - 0.00000036 * jd)                 # obliquity
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * jd) % 24           # hours
    lst = (gmst * 15 + lon) % 360                                 # degrees
    ha = math.radians(lst - ra)
    lat_r = math.radians(lat)
    sin_el = math.sin(lat_r) * math.sin(dec) + math.cos(lat_r) * math.cos(dec) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Neighbourhood polygons
# --------------------------------------------------------------------------
def load_zones(path, name_field='name'):
    """[{'name', 'geom', 'prepared'}] from a WGS84 GeoJSON FeatureCollection."""
    from django.contrib.gis.geos import GEOSGeometry
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    zones = []
    for feature in data.get('features', []):
        name = feature.get('properties', {}).get(name_field)
        if not name or not feature.get('geometry'):
            continue
        geom = GEOSGeometry(json.dumps(feature['geometry']), srid=4326)
        zones.append({'name': str(name), 'geom': geom, 'prepared': geom.prepared})
    if not zones:
        raise ValueError(f'No polygons with property "{name_field}" in {path}')
    return zones


def fetch_city_zones(city, dest_dir=ZONES_DIR):
    """Download the official neighbourhood polygons of ``city`` and store a
    cleaned copy (name/kind/id properties only, 6-decimal coordinates)."""
    import requests
    src = CITY_ZONES_SOURCES[city]
    response = requests.get(src['url'], timeout=120)
    response.raise_for_status()
    data = response.json()

    def rnd(coords):
        if isinstance(coords[0], list):
            return [rnd(c) for c in coords]
        return [round(coords[0], 6), round(coords[1], 6)]

    features = []
    for f in data['features']:
        title = str(f['properties'].get(src['name_field'], ''))
        name, kind = title, ''
        for prefix in src.get('strip_prefixes', ()):
            if title.startswith(prefix):
                name, kind = title[len(prefix):], prefix.strip().split()[-1].lower()
        geom = f['geometry']
        geom['coordinates'] = rnd(geom['coordinates'])
        features.append({'type': 'Feature', 'geometry': geom,
                         'properties': {'name': name, 'kind': kind, 'id': f['properties'].get('id')}})
    out = {'type': 'FeatureCollection', 'name': f'{city} neighbourhoods',
           'source': f"{src['url']} · {src['attribution']}", 'features': features}
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, CITY_ZONES[city])
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(',', ':'))
    return path, len(features)


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------
@dataclass
class LightStats:
    """Lux / CCT statistics over a set of night readings."""
    n_night: int = 0
    n_lux: int = 0
    lux_median: float = None
    lux_p25: float = None
    lux_p75: float = None
    lux_mean: float = None
    lux_bands: list = field(default_factory=list)        # [(label, count, pct)]
    lux_pct_over_30: float = None
    n_cct: int = 0
    cct_median: float = None
    cct_bands: list = field(default_factory=list)
    cct_pct_le_3000: float = None
    cct_pct_le_2700: float = None
    cct_pct_over_4000: float = None
    n_places: int = 0          # rest-of-OpenRed zones: qualifying places aggregated


@dataclass
class UserStats:
    user_id: int
    username: str
    email: str
    display_name: str
    n_measurements: int = 0
    n_night: int = 0
    tracks: set = field(default_factory=set)
    km: float = 0.0
    nights: set = field(default_factory=set)
    lux_median: float = None
    cct_median: float = None

    @property
    def n_tracks(self):
        return len(self.tracks)

    @property
    def n_nights(self):
        return len(self.nights)


@dataclass
class AreaSummary:
    center: tuple                 # (lat, lon)
    place_name: str
    radius_km: float
    since: date                   # local dates, inclusive
    until: date
    tz: str
    twilight_elevation: float
    project_name: str
    n_measurements: int = 0
    n_tracks: int = 0
    km: float = 0.0
    n_users: int = 0
    nights: set = field(default_factory=set)
    first_local: datetime = None
    last_local: datetime = None
    max_distance_km: float = 0.0
    stats: LightStats = field(default_factory=LightStats)
    weather: dict = field(default_factory=dict)
    rings: list = field(default_factory=list)   # [(label, LightStats)] by distance to centre
    zones: dict = field(default_factory=dict)   # group ('city'|'town'|'rural') -> LightStats, this area
    rest: LightStats = None       # rest of OpenRed, same night filter, all time
    rest_n_measurements: int = 0
    rest_zones: dict = field(default_factory=dict)   # group -> LightStats over qualifying places
    rest_places: list = field(default_factory=list)  # [{'cell','lat','lon','n_night','n_tracks','groups':{g:n}}]
    neighbourhoods: list = field(default_factory=list)  # [{'name','geom','n','n_users','stats'}] sorted by n desc
    n_outside_zones: int = 0      # readings in the area but in no neighbourhood polygon
    users: dict = field(default_factory=dict)   # user_id -> UserStats
    lux_values_night: list = field(default_factory=list)
    cct_values_night: list = field(default_factory=list)
    baseline: 'AreaSummary' = None   # same area/since, ending at the previous email's date: drives the "changes" figures

    @property
    def n_nights(self):
        return len(self.nights)

    def recipients(self):
        """UserStats with an email, sorted by measurements desc."""
        return sorted((u for u in self.users.values() if u.email),
                      key=lambda u: -u.n_measurements)


# --------------------------------------------------------------------------
# Computation
# --------------------------------------------------------------------------
def _pct(part, total):
    return round(100.0 * part / total, 1) if total else None


def _bands(values, bands):
    """[{'label', 'n', 'pct', 'bar'}]; ``bar`` is the width (0-100) relative to the biggest band."""
    total = len(values)
    out = []
    for label, lo, hi in bands:
        n = sum(1 for v in values if lo <= v < hi)
        out.append({'label': label, 'n': n, 'pct': _pct(n, total) or 0.0, 'bar': 0})
    top = max((b['n'] for b in out), default=0)
    for b in out:
        b['bar'] = round(100.0 * b['n'] / top) if top else 0
    return out


def light_stats(lux_values, cct_values):
    """LightStats for night readings; ``cct_values`` are filtered to the valid range."""
    s = LightStats(n_night=len(lux_values))
    lux = [v for v in lux_values if v is not None and v >= 0]
    cct = [v for v in cct_values if v is not None and CCT_VALID_RANGE[0] < v < CCT_VALID_RANGE[1]]
    if lux:
        q = statistics.quantiles(lux, n=4) if len(lux) >= 2 else [lux[0]] * 3
        s.n_lux = len(lux)
        s.lux_median = statistics.median(lux)
        s.lux_p25, s.lux_p75 = q[0], q[2]
        s.lux_mean = statistics.fmean(lux)
        s.lux_bands = _bands(lux, LUX_BANDS)
        s.lux_pct_over_30 = _pct(sum(1 for v in lux if v >= 30), len(lux))
    if cct:
        s.n_cct = len(cct)
        s.cct_median = statistics.median(cct)
        s.cct_bands = _bands(cct, CCT_BANDS)
        s.cct_pct_le_3000 = _pct(sum(1 for v in cct if v <= 3000), len(cct))
        s.cct_pct_le_2700 = _pct(sum(1 for v in cct if v <= 2700), len(cct))
        s.cct_pct_over_4000 = _pct(sum(1 for v in cct if v > 4000), len(cct))
    return s


def _period_bounds_utc(since, until, tz):
    """[since 00:00, until 24:00) in local tz, as UTC datetimes."""
    zone = ZoneInfo(tz)
    start = datetime.combine(since, time.min, tzinfo=zone).astimezone(dt_timezone.utc)
    end = datetime.combine(until + timedelta(days=1), time.min, tzinfo=zone).astimezone(dt_timezone.utc)
    return start, end


def _bbox(center, radius_km):
    lat, lon = center
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


_ROW_SQL = """
SELECT m.id, m."dateTime", m.latitude::float, m.longitude::float, m.lux, m.cct,
       m.user_id, m.track_id, m.urban_class,
       w.temperature, w.humidity, w.cloud_cover, w.wind_speed, w.rain_sum
FROM measures_light_pollution_measurement m
LEFT JOIN measures_weathercache w ON w.id = m.weather_cache_id
WHERE m."dateTime" >= %s AND m."dateTime" < %s
  AND m.latitude BETWEEN %s AND %s AND m.longitude BETWEEN %s AND %s
"""

_REST_SQL = """
SELECT m."dateTime", m.latitude::float, m.longitude::float, m.lux, m.cct, m.urban_class, m.track_id
FROM measures_light_pollution_measurement m
WHERE NOT (m.latitude BETWEEN %s AND %s AND m.longitude BETWEEN %s AND %s)
"""


def compute_area_summary(center, radius_km, since, until, place_name=None,
                         project_name=None, tz='Europe/Madrid',
                         twilight_elevation=DEFAULT_TWILIGHT_ELEVATION,
                         rings=DEFAULT_RINGS, compare_rest=True, zones=None):
    """
    Build the AreaSummary for light-pollution measurements within
    ``radius_km`` of ``center`` whose local date is in [since, until].
    ``project_name`` restricts to one project (default: all projects).
    ``zones`` (from ``load_zones``) adds per-neighbourhood statistics.
    """
    from django.contrib.auth import get_user_model
    from missions.models import Project
    from .models import Track

    project_sql, project_params = '', []
    if project_name:
        project = Project.objects.filter(name=project_name).first()
        if project is None:
            raise ValueError(f"Project '{project_name}' not found")
        project_sql, project_params = ' AND m.project_id = %s', [project.id]

    zone = ZoneInfo(tz)
    start_utc, end_utc = _period_bounds_utc(since, until, tz)
    s_lat, n_lat, w_lon, e_lon = _bbox(center, radius_km)

    summary = AreaSummary(center=center, place_name=place_name or f'{center[0]:.3f}, {center[1]:.3f}',
                          radius_km=radius_km, since=since, until=until, tz=tz,
                          twilight_elevation=twilight_elevation, project_name=project_name)

    with connection.cursor() as cur:
        cur.execute(_ROW_SQL + project_sql, [start_utc, end_utc, s_lat, n_lat, w_lon, e_lon] + project_params)
        rows = cur.fetchall()

    per_user_lux = defaultdict(list)
    per_user_cct = defaultdict(list)
    track_ids = set()
    weather_acc = defaultdict(list)
    user_ids = set()
    ring_edges = list(rings) + [float('inf')]
    ring_lux = [[] for _ in ring_edges]
    ring_cct = [[] for _ in ring_edges]

    zone_lux = defaultdict(list)
    zone_cct = defaultdict(list)
    hood = {z['name']: {'name': z['name'], 'geom': z['geom'], 'n': 0, 'users': set(), 'lux': [], 'cct': []}
            for z in (zones or [])}
    hood_prepared = [(z['name'], z['prepared']) for z in (zones or [])]
    if hood_prepared:
        from django.contrib.gis.geos import Point

    for (mid, dt, lat, lon, lux, cct, user_id, track_id, urban_class,
         temp, hum, cloud, wind, rain) in rows:
        dist = haversine_km(center[0], center[1], lat, lon)
        if dist > radius_km:
            continue
        summary.n_measurements += 1
        summary.max_distance_km = max(summary.max_distance_km, dist)
        local = dt.astimezone(zone)
        # A "night" is identified by the local date at 12:00 roll-over: readings
        # after midnight belong to the evening before.
        night_key = (local - timedelta(hours=12)).date()
        summary.nights.add(night_key)
        summary.first_local = local if summary.first_local is None else min(summary.first_local, local)
        summary.last_local = local if summary.last_local is None else max(summary.last_local, local)
        if track_id:
            track_ids.add(track_id)
        for key, val in (('temperature', temp), ('humidity', hum), ('cloud_cover', cloud),
                         ('wind_speed', wind), ('rain_sum', rain)):
            if val is not None:
                weather_acc[key].append(val)

        is_night = solar_elevation(lat, lon, dt) <= twilight_elevation
        if hood_prepared:
            pt = Point(lon, lat, srid=4326)
            for name, prepared in hood_prepared:
                if prepared.contains(pt):
                    h = hood[name]
                    h['n'] += 1
                    if is_night:   # the neighbourhood table is night-only, people included
                        h['users'].add(user_id)
                        h['lux'].append(lux)
                        h['cct'].append(cct)
                    break
            else:
                summary.n_outside_zones += 1
        if is_night:
            summary.lux_values_night.append(lux)
            summary.cct_values_night.append(cct)
            ring_idx = next(i for i, edge in enumerate(ring_edges) if dist <= edge)
            ring_lux[ring_idx].append(lux)
            ring_cct[ring_idx].append(cct)
            group = group_of(urban_class)
            if group:
                zone_lux[group].append(lux)
                zone_cct[group].append(cct)

        if user_id:
            user_ids.add(user_id)
            u = summary.users.get(user_id)
            if u is None:
                u = summary.users[user_id] = UserStats(user_id=user_id, username='', email='', display_name='')
            u.n_measurements += 1
            u.nights.add(night_key)
            if track_id:
                u.tracks.add(track_id)
            if is_night:
                u.n_night += 1
                per_user_lux[user_id].append(lux)
                per_user_cct[user_id].append(cct)

    # Users
    User = get_user_model()
    for user in User.objects.filter(id__in=user_ids):
        u = summary.users[user.id]
        u.username = user.username
        u.email = (user.email or '').strip() if user.is_active else ''
        u.display_name = (user.first_name or '').strip() or user.username
    summary.n_users = len(summary.users)

    # Tracks and kilometres (a track counts in full if any point falls in the area)
    track_km = {}
    for t in Track.objects.filter(id__in=track_ids).values('id', 'total_distance'):
        track_km[t['id']] = (t['total_distance'] or 0.0) / 1000.0
    summary.n_tracks = len(track_ids)
    summary.km = sum(track_km.values())
    for u in summary.users.values():
        u.km = sum(track_km.get(tid, 0.0) for tid in u.tracks)
        lux_u = [v for v in per_user_lux[u.user_id] if v is not None and v >= 0]
        cct_u = [v for v in per_user_cct[u.user_id]
                 if v is not None and CCT_VALID_RANGE[0] < v < CCT_VALID_RANGE[1]]
        u.lux_median = statistics.median(lux_u) if lux_u else None
        u.cct_median = statistics.median(cct_u) if cct_u else None

    # Global light stats and weather
    summary.stats = light_stats(summary.lux_values_night, summary.cct_values_night)
    a, b = (rings[0], rings[-1]) if rings else (0, 0)
    summary.rings = [
        (RING_LABELS_ES[min(i, 2)].format(a=_num(a), b=_num(b)), light_stats(ring_lux[i], ring_cct[i]))
        for i in range(len(ring_edges))
    ]
    summary.zones = {g: light_stats(zone_lux[g], zone_cct[g]) for g in GROUP_ORDER if zone_lux[g]}
    summary.neighbourhoods = sorted(
        ({'name': h['name'], 'geom': h['geom'], 'n': h['n'], 'n_users': len(h['users'] - {None}),
          'stats': light_stats(h['lux'], h['cct'])} for h in hood.values()),
        key=lambda h: -h['n'])
    summary.weather = {k: statistics.fmean(v) for k, v in weather_acc.items() if v}
    summary.weather['coverage_pct'] = _pct(len(weather_acc.get('temperature', [])), summary.n_measurements)

    # Rest of OpenRed (outside the bbox, all time), same night filter
    if compare_rest:
        _compute_rest(summary, project_sql, project_params, (s_lat, n_lat, w_lon, e_lon), twilight_elevation)

    return summary


def _compute_rest(summary, project_sql, project_params, bbox, twilight_elevation):
    """
    Rest of OpenRed (outside the area bbox, all time), night readings only:
    overall stats, plus like-with-like stats per urban group restricted to
    "places" (H3 res 5 cells) with enough readings and tracks.
    """
    import h3

    s_lat, n_lat, w_lon, e_lon = bbox
    rest_lux, rest_cct = [], []
    n_rest = 0
    places = {}
    with connection.cursor() as cur:
        cur.execute(_REST_SQL + project_sql, [s_lat, n_lat, w_lon, e_lon] + project_params)
        for dt, lat, lon, lux, cct, urban_class, track_id in cur.fetchall():
            n_rest += 1
            if solar_elevation(lat, lon, dt) > twilight_elevation:
                continue
            rest_lux.append(lux)
            rest_cct.append(cct)
            cell = h3.latlng_to_cell(lat, lon, REST_PLACE_H3_RES)
            place = places.get(cell)
            if place is None:
                place = places[cell] = {'cell': cell, 'n_night': 0, 'tracks': set(),
                                        'lux': defaultdict(list), 'cct': defaultdict(list)}
            place['n_night'] += 1
            if track_id:
                place['tracks'].add(track_id)
            group = group_of(urban_class)
            if group:
                place['lux'][group].append(lux)
                place['cct'][group].append(cct)

    summary.rest = light_stats(rest_lux, rest_cct)
    summary.rest_n_measurements = n_rest

    zone_lux, zone_cct = defaultdict(list), defaultdict(list)
    zone_places = defaultdict(int)
    for cell, place in places.items():
        qualifies = (place['n_night'] >= REST_PLACE_MIN_NIGHT
                     and len(place['tracks']) >= REST_PLACE_MIN_TRACKS)
        lat, lon = h3.cell_to_latlng(cell)
        summary.rest_places.append({
            'cell': cell, 'lat': lat, 'lon': lon, 'n_night': place['n_night'],
            'n_tracks': len(place['tracks']), 'qualifies': qualifies,
            'groups': {g: len(v) for g, v in place['lux'].items()},
        })
        if not qualifies:
            continue
        for g in place['lux']:
            zone_lux[g].extend(place['lux'][g])
            zone_cct[g].extend(place['cct'][g])
            zone_places[g] += 1
    summary.rest_places.sort(key=lambda p: -p['n_night'])
    summary.rest_zones = {}
    for g in GROUP_ORDER:
        if zone_lux[g]:
            st = light_stats(zone_lux[g], zone_cct[g])
            st.n_places = zone_places[g]
            summary.rest_zones[g] = st


# --------------------------------------------------------------------------
# Charts (reuse the PDF report generators; returned as PNG bytes)
# --------------------------------------------------------------------------
def _data_uri_to_bytes(uri):
    if not uri:
        return None
    return base64.b64decode(uri.split(',', 1)[1])


def render_area_summary_charts(summary, lang='es'):
    """{'lux', 'cct', 'zones'} -> PNG bytes or None, rendered in ``lang``."""
    from django.utils import translation
    from .report_generators import generate_cct_histogram, generate_lux_histogram, generate_polygon_map
    with translation.override(lang):
        zones_png = None
        if summary.neighbourhoods:
            polygons = [{'geom': h['geom'], 'label': h['name'],
                         'value': h['stats'].lux_median if h['stats'].n_lux >= NEIGHBOURHOOD_MIN_NIGHT else None}
                        for h in summary.neighbourhoods]
            zones_png = _data_uri_to_bytes(generate_polygon_map(polygons))
        # Below 0.01 lux the sensor is at its noise floor; keep the log axis readable.
        lux_png = _data_uri_to_bytes(generate_lux_histogram(
            [v for v in summary.lux_values_night if v is not None and v >= 0.01]))
        cct_png = _data_uri_to_bytes(generate_cct_histogram(summary.cct_values_night))
    return {'lux': lux_png, 'cct': cct_png, 'zones': zones_png}


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
MONTHS_ES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
             'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def format_period_es(since, until):
    if since == until:
        return f'{since.day} de {MONTHS_ES[since.month - 1]} de {since.year}'
    if since.year == until.year and since.month == until.month:
        return f'del {since.day} al {until.day} de {MONTHS_ES[since.month - 1]} de {since.year}'
    if since.year == until.year:
        return (f'del {since.day} de {MONTHS_ES[since.month - 1]} '
                f'al {until.day} de {MONTHS_ES[until.month - 1]} de {since.year}')
    return (f'del {since.day} de {MONTHS_ES[since.month - 1]} de {since.year} '
            f'al {until.day} de {MONTHS_ES[until.month - 1]} de {until.year}')


def _num(value, decimals=0):
    """Spanish number formatting: 6.723 / 15,8."""
    if value is None:
        return '—'
    if decimals == 0:
        return f'{int(round(value)):,}'.replace(',', '.')
    s = f'{value:,.{decimals}f}'
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')


def _delta(new, old, decimals=0):
    """'+1.234' text for a count that grew since the baseline; None when nothing changed."""
    diff = (new or 0) - (old or 0)
    if round(diff, decimals) <= 0:
        return None
    return '+' + _num(diff, decimals)


def _changes_context(summary, user_stats):
    """Context keys with the changes since ``summary.baseline`` (empty without a baseline)."""
    base = summary.baseline
    if base is None:
        return {'has_baseline': False}
    st, bst = summary.stats, base.stats
    b_user = base.users.get(user_stats.user_id) if user_stats else None
    b_hoods = {h['name']: h for h in base.neighbourhoods}
    b_rings = dict(base.rings)
    ctx = {
        'has_baseline': True,
        'baseline_date': f'{base.until:%d/%m}',
        'd_measurements': _delta(summary.n_measurements, base.n_measurements),
        'd_users': _delta(summary.n_users, base.n_users),
        'd_tracks': _delta(summary.n_tracks, base.n_tracks),
        'd_km': _delta(summary.km, base.km, 1),
        'd_nights': _delta(summary.n_nights, base.n_nights),
        'prev_lux_median': _num(bst.lux_median, 1) if bst.lux_median is not None else None,
        'prev_cct_median': _num(bst.cct_median) if bst.cct_median is not None else None,
        'prev_lux_pct_over_30': _num(bst.lux_pct_over_30),
        'prev_cct_pct_le_3000': _num(bst.cct_pct_le_3000),
        'hood_deltas': {},
        'ring_deltas': {},
    }
    if user_stats:
        ctx.update({
            'you_new': b_user is None,
            'd_you_measurements': _delta(user_stats.n_measurements, b_user.n_measurements if b_user else 0),
            'd_you_tracks': _delta(user_stats.n_tracks, b_user.n_tracks if b_user else 0),
            'd_you_km': _delta(user_stats.km, b_user.km if b_user else 0, 1),
            'd_you_nights': _delta(user_stats.n_nights, b_user.n_nights if b_user else 0),
        })
    for name, h in ((h['name'], h) for h in summary.neighbourhoods):
        b = b_hoods.get(name)
        b_n = b['stats'].n_lux if b else 0
        ctx['hood_deltas'][name] = {
            'd': _delta(h['stats'].n_lux, b_n),
            'new': b_n < NEIGHBOURHOOD_MIN_NIGHT <= h['stats'].n_lux,
        }
    for label, r in summary.rings:
        ctx['ring_deltas'][label] = _delta(r.n_lux, b_rings[label].n_lux if label in b_rings else 0)
    return ctx


def email_context(summary, user_stats, lang='es', charts=None):
    """Template context for one recipient."""
    from django.conf import settings
    st = summary.stats
    w = summary.weather
    ctx = {
        'lang': lang,
        'site_url': getattr(settings, 'FRONTEND_URL', 'https://map.open-red.es'),
        'place': summary.place_name,
        'radius_km': _num(summary.radius_km),
        'period': format_period_es(summary.since, summary.until),
        'period_short': f'{summary.since:%d/%m} – {summary.until:%d/%m/%Y}',
        'you': user_stats,
        'you_measurements': _num(user_stats.n_measurements) if user_stats else None,
        'you_tracks': user_stats.n_tracks if user_stats else None,
        'you_km': _num(user_stats.km, 1) if user_stats else None,
        'you_nights': user_stats.n_nights if user_stats else None,
        'you_lux_median': _num(user_stats.lux_median, 1) if user_stats and user_stats.lux_median is not None else None,
        'you_cct_median': _num(user_stats.cct_median) if user_stats and user_stats.cct_median is not None else None,
        'n_users': summary.n_users,
        'n_measurements': _num(summary.n_measurements),
        'n_tracks': summary.n_tracks,
        'km': _num(summary.km, 1),
        'n_nights': summary.n_nights,
        'n_night_measurements': _num(st.n_night),
        'pct_night': _pct(st.n_night, summary.n_measurements),
        'lux_median': _num(st.lux_median, 1),
        'lux_p25': _num(st.lux_p25, 1),
        'lux_p75': _num(st.lux_p75, 1),
        'lux_pct_over_30': _num(st.lux_pct_over_30),
        'lux_bands': st.lux_bands,
        'cct_median': _num(st.cct_median),
        'cct_pct_le_3000': _num(st.cct_pct_le_3000),
        'cct_pct_le_2700': _num(st.cct_pct_le_2700),
        'cct_pct_over_4000': _num(st.cct_pct_over_4000),
        'cct_bands': st.cct_bands,
        'has_weather': bool(w.get('temperature') is not None),
        'temperature': _num(w.get('temperature'), 1),
        'humidity': _num(w.get('humidity')),
        'cloud_cover': _num(w.get('cloud_cover')),
        'wind_speed': _num(w.get('wind_speed')),
        'rings': [
            {'label': label, 'n': _num(r.n_lux), 'lux_median': _num(r.lux_median, 1),
             'cct_median': _num(r.cct_median), 'pct_over_30': _num(r.lux_pct_over_30)}
            for label, r in summary.rings if r.n_lux >= 20
        ],
        'zones': [
            {
                'label': GROUP_LABELS_ES[g],
                'area_n': _num(summary.zones[g].n_lux),
                'area_lux': _num(summary.zones[g].lux_median, 1),
                'area_cct': _num(summary.zones[g].cct_median),
                'rest': g in summary.rest_zones,
                'rest_n': _num(summary.rest_zones[g].n_lux) if g in summary.rest_zones else None,
                'rest_lux': _num(summary.rest_zones[g].lux_median, 1) if g in summary.rest_zones else None,
                'rest_cct': _num(summary.rest_zones[g].cct_median) if g in summary.rest_zones else None,
                'rest_places': summary.rest_zones[g].n_places if g in summary.rest_zones else 0,
            }
            for g in GROUP_ORDER if g in summary.zones and summary.zones[g].n_lux >= 20
        ],
        'rest_min_night': _num(REST_PLACE_MIN_NIGHT),
        'neighbourhoods': [
            {'name': h['name'], 'n': _num(h['stats'].n_lux), 'n_users': h['n_users'],
             'lux_median': _num(h['stats'].lux_median, 1), 'cct_median': _num(h['stats'].cct_median),
             'pct_over_30': _num(h['stats'].lux_pct_over_30)}
            for h in summary.neighbourhoods if h['stats'].n_lux >= NEIGHBOURHOOD_MIN_NIGHT
        ],
        'neighbourhood_min_night': _num(NEIGHBOURHOOD_MIN_NIGHT),
        'has_chart_zones': bool(charts and charts.get('zones')),
        'has_rest': summary.rest is not None and summary.rest.n_lux > 0,
        'rest_measurements': _num(summary.rest_n_measurements),
        'rest_lux_median': _num(summary.rest.lux_median, 1) if summary.rest else None,
        'rest_cct_median': _num(summary.rest.cct_median) if summary.rest else None,
        'rest_cct_pct_le_3000': _num(summary.rest.cct_pct_le_3000) if summary.rest else None,
        'has_chart_lux': bool(charts and charts.get('lux')),
        'has_chart_cct': bool(charts and charts.get('cct')),
        'twilight_elevation': _num(abs(summary.twilight_elevation)),
    }
    changes = _changes_context(summary, user_stats)
    hood_deltas = changes.pop('hood_deltas', {})
    ring_deltas = changes.pop('ring_deltas', {})
    ctx.update(changes)
    for h in ctx['neighbourhoods']:
        h.update(hood_deltas.get(h['name'], {}))
    for r in ctx['rings']:
        r['d'] = ring_deltas.get(r['label'])
    return ctx


def build_area_summary_email(summary, user_stats, to_email, lang='es', charts=None, connection=None):
    """
    One personalised message. Charts are attached inline (Content-ID) so they
    display in Gmail/Outlook, which block data: URIs.
    """
    from email.mime.image import MIMEImage
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.template import TemplateDoesNotExist

    ctx = email_context(summary, user_stats, lang=lang, charts=charts)
    base = f'emails/light_area_summary.{lang}'
    try:
        subject = render_to_string(f'{base}_subject.txt', ctx).strip()
        text_body = render_to_string(f'{base}.txt', ctx)
        html_body = render_to_string(f'{base}.html', ctx)
    except TemplateDoesNotExist as e:
        raise ValueError(f"No email templates for language '{lang}' ({e})")

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=[to_email],
        connection=connection,
    )
    message.attach_alternative(html_body, 'text/html')
    message.mixed_subtype = 'related'
    for key in ('lux', 'cct', 'zones'):
        png = (charts or {}).get(key)
        if png:
            img = MIMEImage(png, _subtype='png')
            img.add_header('Content-ID', f'<chart_{key}>')
            img.add_header('Content-Disposition', 'inline', filename=f'{key}.png')
            message.attach(img)
    return message


def send_area_summary(summary, lang='es', recipients=None, charts=None):
    """
    Send one email per recipient (UserStats list; defaults to every contributor
    with an email). Returns {'sent': int, 'failed': int, 'errors': [...]}.
    """
    from django.core.mail import get_connection

    recipients = list(recipients) if recipients is not None else summary.recipients()
    if charts is None:
        charts = render_area_summary_charts(summary, lang=lang)
    sent = failed = 0
    errors = []
    conn = get_connection()
    conn.open()
    try:
        for u in recipients:
            try:
                build_area_summary_email(summary, u, u.email, lang=lang, charts=charts, connection=conn).send()
                sent += 1
            except Exception as send_error:
                failed += 1
                errors.append(f'{u.email}: {send_error}')
                logger.error(f"Area summary email to {u.email} failed: {send_error}")
    finally:
        conn.close()
    logger.info(f"Area summary {summary.place_name} {summary.since}..{summary.until}: sent={sent} failed={failed}")
    return {'sent': sent, 'failed': failed, 'errors': errors}
