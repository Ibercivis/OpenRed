"""
Degree of Urbanisation class for measurements (GHS-SMOD, European Commission JRC).

GHS-SMOD is a global 1 km grid where every cell is classified with the
Degree of Urbanisation methodology used by Eurostat / UN. It gives an
objective, cross-country "city / town-suburb / rural" label for a point, so
light-pollution statistics can be compared like with like (city centres with
city centres, rural with rural) instead of by distance to an arbitrary centre.

Raster: GHS_SMOD_E2025_GLOBE_R2023A_54009_1000 (Mollweide, int16, nodata -200).
Download with ``manage.py urban_class fetch``; path in settings.GHS_SMOD_PATH.

Codes (stored raw in ``LightPollutionMeasurement.urban_class``):
    30  urban centre            (city)
    23  dense urban cluster     (town)
    22  semi-dense urban cluster
    21  suburban / peri-urban
    13  rural cluster           (village)
    12  low density rural
    11  very low density rural
    10  water

Public API:
    classify_points([(lat, lon), ...]) -> [code|None, ...]
    group_of(code) -> 'city' | 'town' | 'rural' | None
    assign_pending_urban_class(limit) -> scheduled sweeper (RQ_JOBS)
"""
import logging
import os
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

GHS_SMOD_URL = (
    'https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_SMOD_GLOBE_R2023A/'
    'GHS_SMOD_E2025_GLOBE_R2023A_54009_1000/V1-0/GHS_SMOD_E2025_GLOBE_R2023A_54009_1000_V1_0.zip'
)
GHS_SMOD_FILENAME = 'GHS_SMOD_E2025_GLOBE_R2023A_54009_1000_V1_0.tif'

NODATA_CODES = {None, -200, 0}
WATER = 10

#: Raw code -> comparison group. Three groups are enough for the emails and
#: keep every group populated; the raw code stays in the DB for finer cuts.
GROUPS = {
    30: 'city',
    23: 'town', 22: 'town', 21: 'town',
    13: 'rural', 12: 'rural', 11: 'rural',
}
GROUP_ORDER = ('city', 'town', 'rural')
GROUP_LABELS_ES = {
    'city': 'Ciudad (centro urbano)',
    'town': 'Pueblo o periferia',
    'rural': 'Zona rural',
}
CODE_LABELS_ES = {
    30: 'Centro urbano', 23: 'Núcleo urbano denso', 22: 'Núcleo urbano semidenso',
    21: 'Suburbano / periurbano', 13: 'Pueblo', 12: 'Rural de baja densidad',
    11: 'Rural de muy baja densidad', 10: 'Agua',
}


def raster_path():
    return getattr(settings, 'GHS_SMOD_PATH', None) or os.path.join(
        settings.BASE_DIR, 'data', 'ghsl', GHS_SMOD_FILENAME)


def raster_available():
    return os.path.exists(raster_path())


def group_of(code):
    return GROUPS.get(code)


# One open dataset per process; rasterio datasets are not thread-safe, hence the lock.
_dataset = None
_lock = threading.Lock()


def _open():
    global _dataset
    if _dataset is None:
        import rasterio
        path = raster_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f'GHS-SMOD raster not found at {path}; run manage.py urban_class fetch')
        _dataset = rasterio.open(path)
    return _dataset


def classify_points(latlons):
    """
    GHS-SMOD code for each (lat, lon), or None where the raster has no data
    (outside land masks). Pure lookup: ~1 µs/point once the raster is open.
    """
    latlons = list(latlons)
    if not latlons:
        return []
    from rasterio.warp import transform
    with _lock:
        ds = _open()
        xs, ys = transform('EPSG:4326', ds.crs, [p[1] for p in latlons], [p[0] for p in latlons])
        out = []
        for value in ds.sample(zip(xs, ys)):
            code = int(value[0])
            out.append(None if code in NODATA_CODES else code)
    return out


def assign_pending_urban_class(limit=20000):
    """
    Scheduled sweeper (RQ_JOBS): fill ``urban_class`` on light-pollution
    measurements that still lack it. Same pattern as the weather bucketing:
    ingest code never classifies, this pass picks up whatever is pending.

    Returns {'processed': int, 'classified': int, 'skipped': 'reason'?}.
    """
    from django.db import transaction
    from .models import LightPollutionMeasurement

    if not raster_available():
        logger.warning('assign_pending_urban_class: raster missing, skipping')
        return {'processed': 0, 'classified': 0, 'skipped': 'raster missing'}

    pending = list(
        LightPollutionMeasurement.objects
        .filter(urban_class__isnull=True, latitude__isnull=False, longitude__isnull=False)
        .order_by('id')
        .values_list('id', 'latitude', 'longitude')[:limit]
    )
    if not pending:
        return {'processed': 0, 'classified': 0}

    codes = classify_points((float(lat), float(lon)) for _, lat, lon in pending)
    by_code = {}
    for (mid, _, _), code in zip(pending, codes):
        if code is not None:
            by_code.setdefault(code, []).append(mid)

    classified = 0
    with transaction.atomic():
        for code, ids in by_code.items():
            classified += LightPollutionMeasurement.objects.filter(id__in=ids).update(urban_class=code)
    unclassified = len(pending) - classified
    if unclassified:
        # Points over nodata (sea) stay NULL and would be re-read every run; that
        # is a handful of GPS glitches at most, so we accept it rather than add a
        # sentinel column.
        logger.info(f'assign_pending_urban_class: {unclassified} points without raster data')
    logger.info(f'assign_pending_urban_class: processed={len(pending)} classified={classified}')
    return {'processed': len(pending), 'classified': classified}
