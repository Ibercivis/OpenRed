"""
Chart and map generators for PDF reports.
Images are returned as base64 data URIs so WeasyPrint can embed them inline.
"""
import io
import base64
import logging
import numpy as np
import h3 as h3lib

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
from django.utils.translation import gettext as _, gettext_lazy, ngettext

try:
    import contextily as ctx
    HAS_CONTEXTILY = True
except ImportError:
    HAS_CONTEXTILY = False

logger = logging.getLogger(__name__)

AMBER = '#f59e0b'

# Fallback when settings.OSM_TILE_USER_AGENT is missing (e.g. a gunicorn master
# preloaded with older settings). OSM blocks anonymous agents, so always identify.
DEFAULT_TILE_USER_AGENT = 'OpenRed-API/1.0 (+https://api.open-red.es; noreply@ibercivis.es)'


def _fig_to_uri(fig, dpi=110):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode('utf-8')
    return f'data:image/png;base64,{b64}'


def bbox_to_resolution(bbox):
    """Pick a sensible H3 resolution from the bbox span."""
    if not bbox:
        return 5
    span = max(bbox['north'] - bbox['south'], bbox['east'] - bbox['west'])
    # Approximate: bigger area → lower resolution (larger hexagons)
    if span > 20:  return 3
    if span > 8:   return 4
    if span > 3:   return 5
    if span > 1:   return 6
    if span > 0.4: return 7
    if span > 0.1: return 8
    return 9


#: Above this extent (degrees, ≈150 km) the report map switches from individual
#: points to H3 hexagons, unless the caller forces a mode.
MAP_POINTS_MAX_SPAN_DEG = 1.5
#: Maximum number of individual points fetched/drawn on a report map.
MAP_POINTS_LIMIT = 20000


def data_extent(points):
    """Bounding box (north/south/east/west) of a list of {lat, lon} dicts, or None."""
    if not points:
        return None
    lats = [p['lat'] for p in points]
    lons = [p['lon'] for p in points]
    return {'north': max(lats), 'south': min(lats), 'east': max(lons), 'west': min(lons)}


def choose_map_mode(requested, points, bbox=None):
    """
    Decide between 'points' and 'hexagons' for the report map.

    ``requested`` is the raw query-param value ('auto', 'points', 'hexagons' or
    None). In auto mode: points when there is something to draw and the map
    covers a small area (zoomed in), hexagons otherwise (zoomed out).
    """
    requested = (requested or 'auto').strip().lower()
    if requested in ('points', 'hexagons'):
        return requested
    if not points:
        return 'hexagons'
    extent = bbox or data_extent(points)
    span = max(extent['north'] - extent['south'], extent['east'] - extent['west'])
    return 'points' if span <= MAP_POINTS_MAX_SPAN_DEG else 'hexagons'


def _frame_map(ax, bbox, lats, lons):
    """Set map limits (bbox or data extent + margin) and a lat-corrected aspect."""
    if bbox:
        ax.set_xlim(bbox['west'], bbox['east'])
        ax.set_ylim(bbox['south'], bbox['north'])
    else:
        lat_span = max(lats) - min(lats)
        lon_span = max(lons) - min(lons)
        lat_margin = max(lat_span * 0.12, 0.004)
        lon_margin = max(lon_span * 0.12, 0.006)
        ax.set_xlim(min(lons) - lon_margin, max(lons) + lon_margin)
        ax.set_ylim(min(lats) - lat_margin, max(lats) + lat_margin)
    y0, y1 = ax.get_ylim()
    mid_lat = np.radians((y0 + y1) / 2)
    ax.set_aspect(1 / max(np.cos(mid_lat), 0.2))


def _add_basemap(ax):
    """Draw OSM tiles under the current axes extent; light-blue fallback if offline."""
    if HAS_CONTEXTILY:
        try:
            from django.conf import settings
            ctx.add_basemap(
                ax,
                crs='EPSG:4326',
                source=ctx.providers.OpenStreetMap.Mapnik,
                attribution=False,
                # lowercase key so it overrides contextily's own default
                headers={'user-agent': getattr(settings, 'OSM_TILE_USER_AGENT', DEFAULT_TILE_USER_AGENT)},
            )
            return
        except Exception as e:
            logger.warning(f"Basemap tiles failed (offline?): {e}")
    ax.set_facecolor('#dce8f0')


def _finish_map(fig, ax, sm, value_label, title):
    cb = fig.colorbar(sm, ax=ax, shrink=0.75, pad=0.02)
    cb.set_label(value_label, fontsize=8)
    cb.ax.tick_params(labelsize=7)
    ax.set_xlabel(_('Longitude'), fontsize=8)
    ax.set_ylabel(_('Latitude'), fontsize=8)
    ax.set_title(title, fontsize=10, fontweight='bold', pad=8)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    return _fig_to_uri(fig)


def generate_polygon_map(polygons, value_label=None, title=None, pad_ratio=0.06):
    """
    Choropleth of arbitrary polygons (e.g. city districts) over an OSM basemap.

    polygons: [{'geom': GEOSGeometry (Polygon/MultiPolygon, WGS84), 'label': str,
                'value': float|None}]. Polygons with value None are drawn as a
    grey outline (no data) and do not drive the extent or the colour scale.
    Returns a PNG data URI or None.
    """
    from matplotlib.patches import Polygon as MplPolygon
    if value_label is None:
        value_label = _('Median lux')
    with_data = [p for p in polygons if p.get('value') is not None]
    if not with_data:
        return None
    try:
        fig, ax = plt.subplots(figsize=(9, 6.5))
        fig.patch.set_facecolor('white')
        vals = [p['value'] for p in with_data]
        vmin, vmax = min(vals), max(vals)
        if vmin == vmax:
            vmax = vmin + 1
        cmap = cm.YlOrRd
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

        def rings(geom):
            geoms = geom if geom.geom_type == 'MultiPolygon' else [geom]
            for poly in geoms:
                yield list(poly.exterior_ring.coords)

        # Extent: polygons with data, ignoring area outliers (a huge rural district
        # would otherwise shrink the city to a corner). Outliers are still drawn.
        areas = sorted(p['geom'].area for p in with_data)
        area_cap = areas[len(areas) // 2] * 8
        west = south = float('inf'); east = north = float('-inf')
        for p in with_data:
            if p['geom'].area <= area_cap or len(with_data) == 1:
                x0, y0, x1, y1 = p['geom'].extent
                west, south, east, north = min(west, x0), min(south, y0), max(east, x1), max(north, y1)
        dx = max((east - west) * pad_ratio, 0.002); dy = max((north - south) * pad_ratio, 0.002)
        west, south, east, north = west - dx, south - dy, east + dx, north + dy
        from django.contrib.gis.geos import Polygon as GeosPolygon
        view = GeosPolygon.from_bbox((west, south, east, north))

        for p in polygons:
            has = p.get('value') is not None
            for ring in rings(p['geom']):
                patch = MplPolygon(ring, closed=True,
                                   facecolor=cmap(norm(p['value'])) if has else 'none',
                                   edgecolor='#334155' if has else '#94a3b8',
                                   linewidth=0.9 if has else 0.6,
                                   alpha=0.75 if has else 1.0, zorder=3 if has else 2)
                ax.add_patch(patch)
        for p in with_data:
            visible = p['geom'].intersection(view)
            if visible.empty or visible.area < p['geom'].area * 0.05:
                continue   # (almost) out of view: no dangling label
            c = visible.point_on_surface   # always inside, unlike the centroid of a crescent
            ax.annotate(p['label'], (c.x, c.y), ha='center', va='center', fontsize=7.5,
                        color='#0f172a', zorder=5,
                        bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.75, linewidth=0))
        ax.set_xlim(west, east); ax.set_ylim(south, north)
        ax.set_aspect(1 / np.cos(np.radians((south + north) / 2)))
        _add_basemap(ax)
        sm = cm.ScalarMappable(cmap=cmap, norm=norm); sm.set_array([])
        n = len(with_data)
        if title is None:
            title = ngettext('%(n)s district with data', '%(n)s districts with data', n) % {'n': n}
        return _finish_map(fig, ax, sm, value_label, title)
    except Exception as e:
        logger.error(f"generate_polygon_map failed: {e}", exc_info=True)
        return None


def generate_h3_map(hexagons, resolution, bbox=None, tmpdir=None, value_label=None):
    """
    Choropleth of H3 hexagons coloured by avg_value.
    bbox: dict with keys north, south, east, west (floats) or None.
    When provided it sets the map view and drives the OSM tile zoom.
    """
    if not hexagons:
        return None
    if value_label is None:
        value_label = _('Mean lux')
    try:
        fig, ax = plt.subplots(figsize=(9, 5.5))
        fig.patch.set_facecolor('white')

        vals = [h['avg_value'] for h in hexagons]
        vmin, vmax = min(vals), max(vals)
        if vmin == vmax:
            vmax = vmin + 1

        cmap = cm.YlOrRd
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

        all_lats, all_lons = [], []
        drawn = 0
        for hex_data in hexagons:
            try:
                boundary = h3lib.cell_to_boundary(hex_data['h3_index'])
                lons = [lng for lat, lng in boundary]
                lats = [lat for lat, lng in boundary]
                all_lats.extend(lats)
                all_lons.extend(lons)
                color = cmap(norm(hex_data['avg_value']))
                ax.fill(lons, lats, color=color, alpha=0.75, edgecolor='white', linewidth=0.6)
                drawn += 1
            except Exception as e:
                logger.warning(f"Skipping hexagon {hex_data.get('h3_index')}: {e}")

        if drawn == 0:
            plt.close(fig)
            return None

        _frame_map(ax, bbox, all_lats, all_lons)
        _add_basemap(ax)

        sm = cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        title = _('Spatial distribution · H3 resolution %(resolution)s') % {'resolution': resolution}
        return _finish_map(fig, ax, sm, value_label, title)
    except Exception as e:
        logger.error(f"generate_h3_map failed: {e}", exc_info=True)
        return None


def generate_points_map(points, bbox=None, value_label=None, log_scale=False):
    """
    Scatter of individual measurements coloured by value, over an OSM basemap.
    points: list of {lat, lon, value}. The colour scale is clipped to the
    2nd–98th percentiles so a few outliers do not wash out the rest; with
    log_scale=True (lux) the scale is logarithmic.
    """
    if not points:
        return None
    if value_label is None:
        value_label = _('Lux')
    try:
        lats = np.array([p['lat'] for p in points], dtype=float)
        lons = np.array([p['lon'] for p in points], dtype=float)
        vals = np.array([p['value'] for p in points], dtype=float)

        # Draw the highest values last so hot spots stay visible
        order = np.argsort(vals)
        lats, lons, vals = lats[order], lons[order], vals[order]

        fig, ax = plt.subplots(figsize=(9, 5.5))
        fig.patch.set_facecolor('white')

        cmap = cm.YlOrRd
        positive = vals[vals > 0]
        if log_scale and positive.size:
            vmin = max(np.percentile(positive, 2), 1e-3)
            vmax = max(np.percentile(positive, 98), vmin * 10)
            norm = mcolors.LogNorm(vmin=vmin, vmax=vmax)
            vals = np.clip(vals, vmin, None)
        else:
            vmin, vmax = np.percentile(vals, [2, 98])
            if vmin == vmax:
                vmax = vmin + (abs(vmin) * 0.1 or 1)
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

        n = len(vals)
        size = 48 if n <= 100 else 24 if n <= 1500 else 10 if n <= 6000 else 5
        edge = 0.8 if n <= 100 else 0.35 if n <= 1500 else 0
        ax.scatter(lons, lats, c=vals, cmap=cmap, norm=norm, s=size, alpha=0.85,
                   linewidths=edge, edgecolors='#334155', zorder=3)

        _frame_map(ax, bbox, lats, lons)
        _add_basemap(ax)

        sm = cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        title = ngettext('Spatial distribution · %(n)s measurement',
                         'Spatial distribution · %(n)s measurements', n) % {'n': n}
        return _finish_map(fig, ax, sm, value_label, title)
    except Exception as e:
        logger.error(f"generate_points_map failed: {e}", exc_info=True)
        return None


def generate_cct_histogram(cct_values, tmpdir=None):
    if not cct_values:
        return None
    try:
        vals = [v for v in cct_values if v and 1000 < v < 7500]
        if not vals:
            return None

        fig, ax = plt.subplots(figsize=(8, 3.5))
        fig.patch.set_facecolor('white')

        ax.hist(vals, bins=40, color='#60a5fa', edgecolor='white', linewidth=0.4)
        ax.set_xlabel(_('Colour temperature (K)'), fontsize=9)
        ax.set_ylabel(_('Measurements'), fontsize=9)
        ax.set_title(_('Colour temperature (CCT) distribution'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)

        # Reference lines for common light source types
        for k, label in [(2700, _('Incandescent')), (4000, _('Neutral white')), (6500, _('Daylight'))]:
            if min(vals) <= k <= max(vals):
                ax.axvline(k, color='#475569', linewidth=0.8, linestyle='--', alpha=0.7)
                ax.text(k + 50, ax.get_ylim()[1] * 0.9, label, fontsize=6.5, color='#475569')

        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_cct_histogram failed: {e}", exc_info=True)
        return None


def generate_lux_histogram(lux_values, tmpdir=None):
    if not lux_values:
        return None
    try:
        vals = [v for v in lux_values if v and v > 0]
        if not vals:
            return None

        fig, ax = plt.subplots(figsize=(8, 3.5))
        fig.patch.set_facecolor('white')

        log_min = np.floor(np.log10(min(vals)))
        log_max = np.ceil(np.log10(max(vals)))
        bins = np.logspace(log_min, log_max, 40)

        ax.hist(vals, bins=bins, color=AMBER, edgecolor='white', linewidth=0.4)
        ax.set_xscale('log')
        ax.set_xlabel(_('Lux (log scale)'), fontsize=9)
        ax.set_ylabel(_('Measurements'), fontsize=9)
        ax.set_title(_('Lux value distribution'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_lux_histogram failed: {e}", exc_info=True)
        return None


def generate_time_series(time_data, tmpdir=None):
    if not time_data:
        return None
    try:
        dates  = [d['date']    for d in time_data]
        values = [d['avg_lux'] for d in time_data]

        fig, ax = plt.subplots(figsize=(9, 3.2))
        fig.patch.set_facecolor('white')

        ax.plot(dates, values, color=AMBER, linewidth=1.5, marker='o', markersize=2.5)
        ax.fill_between(dates, values, alpha=0.18, color=AMBER)
        ax.set_xlabel(_('Date'), fontsize=9)
        ax.set_ylabel(_('Mean lux'), fontsize=9)
        ax.set_title(_('Mean lux over time'), fontsize=11, fontweight='bold')

        if len(dates) > 60:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator())
        elif len(dates) > 14:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator())
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))

        plt.xticks(rotation=40, ha='right', fontsize=7)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(axis='y', labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_time_series failed: {e}", exc_info=True)
        return None


def generate_lux_cct_scatter(scatter_data, tmpdir=None):
    if not scatter_data:
        return None
    try:
        lux = [d['lux'] for d in scatter_data]
        cct = [d['cct'] for d in scatter_data]

        fig, ax = plt.subplots(figsize=(8, 3.5))
        fig.patch.set_facecolor('white')

        ax.scatter(lux, cct, alpha=0.3, color=AMBER, s=7, edgecolors='none')
        ax.set_xscale('log')
        ax.set_ylim(1000, 7500)
        ax.set_xlabel(_('Lux (log scale)'), fontsize=9)
        ax.set_ylabel('CCT (K)', fontsize=9)
        ax.set_title(_('Lux vs colour temperature'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_lux_cct_scatter failed: {e}", exc_info=True)
        return None


# IDA light pollution categories (approximate lux thresholds at ground level)
IDA_CATEGORIES = [
    (0.01,  gettext_lazy('Class 1 · Dark sky'),         '#1e3a5f'),
    (0.1,   gettext_lazy('Class 2 · Rural'),            '#1d4ed8'),
    (1.0,   gettext_lazy('Class 3 · Rural/Peri-urban'), '#0891b2'),
    (5.0,   gettext_lazy('Class 4 · Peri-urban'),       '#16a34a'),
    (25.0,  gettext_lazy('Class 5 · Suburban'),         '#ca8a04'),
    (100.0, gettext_lazy('Class 6 · Urban'),            '#ea580c'),
    (float('inf'), gettext_lazy('Class 7 · Heavily polluted'), '#dc2626'),
]


def generate_ida_chart(lux_values, tmpdir=None):
    """Bar chart showing distribution of measurements across IDA pollution categories."""
    if not lux_values:
        return None
    try:
        counts = [0] * len(IDA_CATEGORIES)
        for v in lux_values:
            for i, (threshold, _label, _color) in enumerate(IDA_CATEGORIES):
                if v < threshold:
                    counts[i] += 1
                    break

        labels = [str(c[1]) for c in IDA_CATEGORIES]
        colors = [c[2] for c in IDA_CATEGORIES]
        total = sum(counts)
        pcts = [c / total * 100 for c in counts]

        # Only keep categories with data
        data = [(l, c, p, col) for l, c, p, col in zip(labels, counts, pcts, colors) if c > 0]
        if not data:
            return None

        labels_f, counts_f, pcts_f, colors_f = zip(*data)

        fig, ax = plt.subplots(figsize=(9, 3.5))
        fig.patch.set_facecolor('white')

        bars = ax.barh(labels_f, counts_f, color=colors_f, edgecolor='white', linewidth=0.5)
        for bar, pct in zip(bars, pcts_f):
            ax.text(bar.get_width() + total * 0.005, bar.get_y() + bar.get_height() / 2,
                    f'{pct:.1f}%', va='center', fontsize=8, color='#334155')

        ax.set_xlabel(_('Number of measurements'), fontsize=9)
        ax.set_title(_('IDA light pollution classification'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_ida_chart failed: {e}", exc_info=True)
        return None


def generate_time_series_trend(time_data, tmpdir=None):
    """Time series with linear regression trend line."""
    if not time_data or len(time_data) < 3:
        return None
    try:
        dates  = [d['date']    for d in time_data]
        values = [d['avg_lux'] for d in time_data]

        fig, ax = plt.subplots(figsize=(9, 3.2))
        fig.patch.set_facecolor('white')

        ax.plot(dates, values, color=AMBER, linewidth=1.5, marker='o', markersize=2.5, label=_('Daily mean lux'))
        ax.fill_between(dates, values, alpha=0.18, color=AMBER)

        # Linear trend
        x_num = np.arange(len(dates))
        coeffs = np.polyfit(x_num, values, 1)
        trend = np.polyval(coeffs, x_num)
        slope = coeffs[0]
        trend_color = '#dc2626' if slope > 0 else '#16a34a'
        trend_label = _('Trend (↑ increasing)') if slope > 0 else _('Trend (↓ decreasing)')
        ax.plot(dates, trend, color=trend_color, linewidth=1.5, linestyle='--', alpha=0.8, label=trend_label)

        ax.set_xlabel(_('Date'), fontsize=9)
        ax.set_ylabel(_('Mean lux'), fontsize=9)
        ax.set_title(_('Mean lux over time'), fontsize=11, fontweight='bold')
        ax.legend(fontsize=8, framealpha=0.7)

        if len(dates) > 60:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator())
        elif len(dates) > 14:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator())
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))

        plt.xticks(rotation=40, ha='right', fontsize=7)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(axis='y', labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_time_series_trend failed: {e}", exc_info=True)
        return None


def generate_night_day_chart(hour_data, tmpdir=None):
    """Stacked bar comparing night (22h-6h) vs day (6h-22h) measurements and avg lux."""
    if not hour_data:
        return None
    try:
        NIGHT_HOURS = set(range(0, 6)) | set(range(22, 24))

        night_count = sum(d['count'] for d in hour_data if d['hour'] in NIGHT_HOURS)
        day_count   = sum(d['count'] for d in hour_data if d['hour'] not in NIGHT_HOURS)

        night_lux_data = [d for d in hour_data if d['hour'] in NIGHT_HOURS and d['avg_lux']]
        day_lux_data   = [d for d in hour_data if d['hour'] not in NIGHT_HOURS and d['avg_lux']]

        if not (night_count or day_count):
            return None

        night_avg = (sum(d['avg_lux'] * d['count'] for d in night_lux_data) /
                     sum(d['count'] for d in night_lux_data)) if night_lux_data else 0
        day_avg   = (sum(d['avg_lux'] * d['count'] for d in day_lux_data) /
                     sum(d['count'] for d in day_lux_data)) if day_lux_data else 0

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.2))
        fig.patch.set_facecolor('white')

        labels = [_('Night\n(22h–6h)'), _('Day\n(6h–22h)')]
        colors = ['#1e3a5f', '#f59e0b']

        ax1.bar(labels, [night_count, day_count], color=colors, edgecolor='white', width=0.5)
        ax1.set_title(_('Measurements per period'), fontsize=10, fontweight='bold')
        ax1.set_ylabel(_('Number of measurements'), fontsize=9)
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        ax1.tick_params(labelsize=8)
        for i, v in enumerate([night_count, day_count]):
            ax1.text(i, v + max(night_count, day_count) * 0.02, str(v), ha='center', fontsize=8)

        ax2.bar(labels, [night_avg, day_avg], color=colors, edgecolor='white', width=0.5)
        ax2.set_title(_('Mean lux per period'), fontsize=10, fontweight='bold')
        ax2.set_ylabel(_('Mean lux'), fontsize=9)
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        ax2.tick_params(labelsize=8)
        for i, v in enumerate([night_avg, day_avg]):
            ax2.text(i, v + max(night_avg, day_avg) * 0.02, f'{v:.2f}', ha='center', fontsize=8)

        fig.suptitle(_('Night vs day comparison (UTC)'), fontsize=11, fontweight='bold', y=1.02)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_night_day_chart failed: {e}", exc_info=True)
        return None


def generate_hour_distribution(hour_data, tmpdir=None):
    if not hour_data:
        return None
    try:
        hours     = [d['hour']    for d in hour_data]
        counts    = [d['count']   for d in hour_data]
        avg_luxes = [d['avg_lux'] for d in hour_data]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.5))
        fig.patch.set_facecolor('white')

        ax1.bar(hours, counts, color=AMBER, edgecolor='white', linewidth=0.5, width=0.8)
        ax1.set_xlabel(_('Hour of day (UTC)'), fontsize=9)
        ax1.set_ylabel(_('Number of measurements'), fontsize=9)
        ax1.set_title(_('Measurements per hour'), fontsize=10, fontweight='bold')
        ax1.set_xticks(range(0, 24, 2))
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        ax1.tick_params(labelsize=7)

        ax2.bar(hours, avg_luxes, color='#93c5fd', edgecolor='white', linewidth=0.5, width=0.8)
        ax2.set_xlabel(_('Hour of day (UTC)'), fontsize=9)
        ax2.set_ylabel(_('Mean lux'), fontsize=9)
        ax2.set_title(_('Mean lux per hour'), fontsize=10, fontweight='bold')
        ax2.set_xticks(range(0, 24, 2))
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        ax2.tick_params(labelsize=7)

        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_hour_distribution failed: {e}", exc_info=True)
        return None


# ── RADIATION-SPECIFIC GENERATORS ────────────────────────────────────────────

RAD_GREEN = '#10b981'

RADIATION_CATEGORIES = [
    (0.3,          gettext_lazy('Natural background (<0.3 μSv/h)'), '#16a34a'),
    (1.0,          gettext_lazy('Elevated (0.3–1 μSv/h)'),          '#ca8a04'),
    (10.0,         gettext_lazy('Concerning (1–10 μSv/h)'),         '#ea580c'),
    (100.0,        gettext_lazy('High (10–100 μSv/h)'),             '#dc2626'),
    (float('inf'), gettext_lazy('Extreme (>100 μSv/h)'),            '#7f1d1d'),
]


def generate_dose_rate_histogram(dose_rate_values, tmpdir=None):
    if not dose_rate_values:
        return None
    try:
        vals = [v for v in dose_rate_values if v and v > 0]
        if not vals:
            return None

        fig, ax = plt.subplots(figsize=(8, 3.5))
        fig.patch.set_facecolor('white')

        if max(vals) / max(min(vals), 1e-9) > 100:
            log_min = np.floor(np.log10(min(vals)))
            log_max = np.ceil(np.log10(max(vals)))
            bins = np.logspace(log_min, log_max, 40)
            ax.set_xscale('log')
            ax.set_xlabel(_('Dose rate (μSv/h) — log scale'), fontsize=9)
        else:
            bins = 40
            ax.set_xlabel(_('Dose rate (μSv/h)'), fontsize=9)

        ax.hist(vals, bins=bins, color=RAD_GREEN, edgecolor='white', linewidth=0.4)
        ax.set_ylabel(_('Measurements'), fontsize=9)
        ax.set_title(_('Dose rate distribution (μSv/h)'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)

        bg = 0.2
        if min(vals) <= bg <= max(vals):
            ax.axvline(bg, color='#475569', linewidth=0.8, linestyle='--', alpha=0.7)
            ax.text(bg * 1.05, ax.get_ylim()[1] * 0.9, 'Fondo nat.', fontsize=6.5, color='#475569')

        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_dose_rate_histogram failed: {e}", exc_info=True)
        return None


def generate_cpm_histogram(cpm_values, tmpdir=None):
    if not cpm_values:
        return None
    try:
        vals = [v for v in cpm_values if v and v > 0]
        if not vals:
            return None

        fig, ax = plt.subplots(figsize=(8, 3.5))
        fig.patch.set_facecolor('white')

        ax.hist(vals, bins=40, color='#60a5fa', edgecolor='white', linewidth=0.4)
        ax.set_xlabel(_('Counts per minute (CPM)'), fontsize=9)
        ax.set_ylabel(_('Measurements'), fontsize=9)
        ax.set_title(_('CPM distribution (counts per minute)'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_cpm_histogram failed: {e}", exc_info=True)
        return None


def generate_dose_rate_cpm_scatter(scatter_data, tmpdir=None):
    """Scatter CPM (x) vs dose_rate (y) — verifica linealidad/calibración."""
    if not scatter_data:
        return None
    try:
        cpm  = [d['cpm']       for d in scatter_data]
        dose = [d['dose_rate'] for d in scatter_data]

        fig, ax = plt.subplots(figsize=(8, 3.5))
        fig.patch.set_facecolor('white')

        ax.scatter(cpm, dose, alpha=0.3, color=RAD_GREEN, s=7, edgecolors='none')
        ax.set_xlabel(_('CPM (counts per minute)'), fontsize=9)
        ax.set_ylabel(_('Dose rate (μSv/h)'), fontsize=9)
        ax.set_title(_('CPM vs dose rate'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)

        if len(cpm) >= 3:
            coeffs = np.polyfit(cpm, dose, 1)
            x_range = np.linspace(min(cpm), max(cpm), 100)
            ax.plot(x_range, np.polyval(coeffs, x_range), color='#dc2626', linewidth=1.2,
                    linestyle='--', alpha=0.7, label=_('Factor: %(factor).4f μSv/CPM') % {'factor': coeffs[0]})
            ax.legend(fontsize=8, framealpha=0.7)

        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_dose_rate_cpm_scatter failed: {e}", exc_info=True)
        return None


def generate_radiation_classification_chart(dose_rate_values, tmpdir=None):
    """Barras horizontales con distribución por nivel de radiación."""
    if not dose_rate_values:
        return None
    try:
        counts = [0] * len(RADIATION_CATEGORIES)
        for v in dose_rate_values:
            for i, (threshold, _label, _color) in enumerate(RADIATION_CATEGORIES):
                if v < threshold:
                    counts[i] += 1
                    break

        total = sum(counts)
        if total == 0:
            return None

        labels = [str(c[1]) for c in RADIATION_CATEGORIES]
        colors = [c[2] for c in RADIATION_CATEGORIES]
        pcts   = [c / total * 100 for c in counts]

        data = [(l, c, p, col) for l, c, p, col in zip(labels, counts, pcts, colors) if c > 0]
        if not data:
            return None

        labels_f, counts_f, pcts_f, colors_f = zip(*data)

        fig, ax = plt.subplots(figsize=(9, 3.5))
        fig.patch.set_facecolor('white')

        bars = ax.barh(labels_f, counts_f, color=colors_f, edgecolor='white', linewidth=0.5)
        for bar, pct in zip(bars, pcts_f):
            ax.text(bar.get_width() + total * 0.005, bar.get_y() + bar.get_height() / 2,
                    f'{pct:.1f}%', va='center', fontsize=8, color='#334155')

        ax.set_xlabel(_('Number of measurements'), fontsize=9)
        ax.set_title(_('Classification by radiation level (μSv/h)'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_radiation_classification_chart failed: {e}", exc_info=True)
        return None


def generate_altitude_dose_scatter(data, tmpdir=None):
    """Scatter altitud (x) vs tasa de dosis (y). Muestra efecto de radiación cósmica."""
    if not data:
        return None
    try:
        altitudes = [d['altitude']  for d in data]
        doses     = [d['dose_rate'] for d in data]

        fig, ax = plt.subplots(figsize=(8, 3.5))
        fig.patch.set_facecolor('white')

        ax.scatter(altitudes, doses, alpha=0.3, color=RAD_GREEN, s=7, edgecolors='none')
        ax.set_xlabel(_('Altitude (m)'), fontsize=9)
        ax.set_ylabel(_('Dose rate (μSv/h)'), fontsize=9)
        ax.set_title(_('Altitude vs dose rate'), fontsize=11, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=8)

        if len(altitudes) >= 3:
            coeffs = np.polyfit(altitudes, doses, 1)
            x_range = np.linspace(min(altitudes), max(altitudes), 100)
            slope = coeffs[0]
            trend_color = '#dc2626' if slope > 0 else '#16a34a'
            ax.plot(x_range, np.polyval(coeffs, x_range), color=trend_color, linewidth=1.5,
                    linestyle='--', alpha=0.8,
                    label=_('Trend (↑ increases with altitude)') if slope > 0 else _('Trend (↓ decreases with altitude)'))
            ax.legend(fontsize=8, framealpha=0.7)

        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_altitude_dose_scatter failed: {e}", exc_info=True)
        return None


def generate_radiation_time_series(time_data, tmpdir=None):
    """Serie temporal de dosis media diaria con línea de tendencia."""
    if not time_data or len(time_data) < 2:
        return None
    try:
        dates  = [d['date']         for d in time_data]
        values = [d['avg_dose_rate'] for d in time_data]

        fig, ax = plt.subplots(figsize=(9, 3.2))
        fig.patch.set_facecolor('white')

        ax.plot(dates, values, color=RAD_GREEN, linewidth=1.5, marker='o', markersize=2.5,
                label=_('Daily mean dose'))
        ax.fill_between(dates, values, alpha=0.18, color=RAD_GREEN)

        if len(dates) >= 3:
            x_num = np.arange(len(dates))
            coeffs = np.polyfit(x_num, values, 1)
            trend = np.polyval(coeffs, x_num)
            slope = coeffs[0]
            trend_color = '#dc2626' if slope > 0 else '#16a34a'
            ax.plot(dates, trend, color=trend_color, linewidth=1.5, linestyle='--', alpha=0.8,
                    label=_('Trend (↑ increasing)') if slope > 0 else _('Trend (↓ decreasing)'))

        ax.set_xlabel(_('Date'), fontsize=9)
        ax.set_ylabel(_('Mean dose rate (μSv/h)'), fontsize=9)
        ax.set_title(_('Mean dose rate over time'), fontsize=11, fontweight='bold')
        ax.legend(fontsize=8, framealpha=0.7)

        if len(dates) > 60:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator())
        elif len(dates) > 14:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator())
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))

        plt.xticks(rotation=40, ha='right', fontsize=7)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(axis='y', labelsize=8)
        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_radiation_time_series failed: {e}", exc_info=True)
        return None


def generate_weather_dose_scatter(weather_dose_data, tmpdir=None):
    """
    Panel 2×2 de scatter: dose_rate vs temperatura, presión, cobertura nubosa, precipitación.
    Incluye línea de tendencia en cada panel.
    """
    if not weather_dose_data:
        return None
    try:
        PANELS = [
            ('temperature', _('Temperature'),   '°C',  '#ef4444'),
            ('pressure',    _('Pressure'),      'hPa', '#a78bfa'),
            ('cloud_cover', _('Cloud cover'),   '%',   '#60a5fa'),
            ('rain_sum',    _('Precipitation'), 'mm',  '#34d399'),
        ]
        # Keep only panels that have enough data points
        active = [
            (field, label, unit, color)
            for field, label, unit, color in PANELS
            if sum(1 for d in weather_dose_data if d.get(field) is not None) >= 10
        ]
        if not active:
            return None

        ncols = 2
        nrows = (len(active) + 1) // 2
        fig, axes = plt.subplots(nrows, ncols, figsize=(10, 3.8 * nrows))
        fig.patch.set_facecolor('white')

        # Normalise axes to always be 2-D list
        if nrows == 1 and ncols == 1:
            axes = [[axes]]
        elif nrows == 1:
            axes = [list(axes)]
        elif ncols == 1:
            axes = [[ax] for ax in axes]
        else:
            axes = [list(row) for row in axes]

        for idx, (field, label, unit, color) in enumerate(active):
            row, col = divmod(idx, ncols)
            ax = axes[row][col]

            pairs = [(d['dose_rate'], d[field]) for d in weather_dose_data if d.get(field) is not None]
            dose_v, x_v = zip(*pairs)

            ax.scatter(x_v, dose_v, alpha=0.25, color=color, s=6, edgecolors='none')
            ax.set_xlabel(f'{label} ({unit})', fontsize=9)
            ax.set_ylabel(_('Dose rate (μSv/h)'), fontsize=9)
            ax.set_title(_('%(variable)s vs dose') % {'variable': label}, fontsize=10, fontweight='bold')
            ax.set_ylim(0, 1)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.tick_params(labelsize=7)

            if len(x_v) >= 3:
                coeffs = np.polyfit(x_v, dose_v, 1)
                x_range = np.linspace(min(x_v), max(x_v), 100)
                ax.plot(x_range, np.polyval(coeffs, x_range),
                        color='#334155', linewidth=1.2, linestyle='--', alpha=0.7)

        # Hide unused axes
        for idx in range(len(active), nrows * ncols):
            row, col = divmod(idx, ncols)
            axes[row][col].set_visible(False)

        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_weather_dose_scatter failed: {e}", exc_info=True)
        return None


def generate_radiation_hour_distribution(hour_data, tmpdir=None):
    """Doble barra: medidas por hora + dosis media por hora."""
    if not hour_data:
        return None
    try:
        hours     = [d['hour']          for d in hour_data]
        counts    = [d['count']         for d in hour_data]
        avg_doses = [d['avg_dose_rate'] for d in hour_data]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.5))
        fig.patch.set_facecolor('white')

        ax1.bar(hours, counts, color=RAD_GREEN, edgecolor='white', linewidth=0.5, width=0.8)
        ax1.set_xlabel(_('Hour of day (UTC)'), fontsize=9)
        ax1.set_ylabel(_('Number of measurements'), fontsize=9)
        ax1.set_title(_('Measurements per hour'), fontsize=10, fontweight='bold')
        ax1.set_xticks(range(0, 24, 2))
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        ax1.tick_params(labelsize=7)

        ax2.bar(hours, avg_doses, color='#6ee7b7', edgecolor='white', linewidth=0.5, width=0.8)
        ax2.set_xlabel(_('Hour of day (UTC)'), fontsize=9)
        ax2.set_ylabel(_('Mean dose rate (μSv/h)'), fontsize=9)
        ax2.set_title(_('Mean dose per hour'), fontsize=10, fontweight='bold')
        ax2.set_xticks(range(0, 24, 2))
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        ax2.tick_params(labelsize=7)

        fig.tight_layout()
        return _fig_to_uri(fig)
    except Exception as e:
        logger.error(f"generate_radiation_hour_distribution failed: {e}", exc_info=True)
        return None
