"""Export a user's own tracks as a single ZIP (CSV files + original uploads)."""
import csv
import io
import os
import tempfile
import zipfile

from .models import RadiationMeasurement, Spectrum

TRACK_FIELDS = [
    'id', 'project_id', 'mission_id', 'campaign_id', 'device_id', 'file_type',
    'description', 'start_time', 'end_time', 'total_measurements', 'total_distance',
    'average_speed', 'min_dose_rate', 'max_dose_rate', 'avg_dose_rate', 'std_dose_rate',
    'status', 'client_track_id', 'created_at',
]
MEASUREMENT_FIELDS = [
    'track_id', 'measurement_id', 'dateTime', 'timestamp', 'latitude', 'longitude', 'altitude',
    'accuracy', 'speed', 'dose_rate', 'dose_rate_error', 'dose_rate_rel_error',
    'radiation_unit', 'cpm', 'cpm_error', 'cpm_rel_error', 'detector_type',
    'calibration_factor', 'background_subtracted', 'data_quality', 'capture_mode', 'notes',
]
SPECTRUM_META = [
    'id', 'track_id', 'name', 'index', 'started_at', 'ended_at', 'duration_sec',
    'a0', 'a1', 'a2', 'channel_count', 'start_lat', 'start_lon', 'start_alt',
    'end_lat', 'end_lon',
]

README = """OpenRed track export
====================

This archive contains your own tracks exported from OpenRed (https://map.open-red.es).
Only tracks created by your account are included.

Files
-----
README.txt         this file
tracks.csv         one row per track (metadata and summary statistics)
measurements.csv   one row per radiation measurement
spectra.csv        one row per gamma spectrum (RadiaCode devices only; may be absent)
originals/         the files exactly as you originally uploaded them (when stored)

General notes
-------------
- Encoding UTF-8, comma-separated, first row is the header.
- Timestamps are ISO 8601 and in UTC.
- An empty cell means "no value".
- Files are linked through track_id (measurements.csv, spectra.csv) = id (tracks.csv).

tracks.csv
----------
id                  track identifier
project_id, mission_id, campaign_id, device_id   related entities
file_type           format of the uploaded file (rctrk, json)
description         free-text description
start_time, end_time   first and last measurement of the track
total_measurements  number of measurements
total_distance      distance covered
average_speed       mean speed
min/max/avg/std_dose_rate   dose-rate statistics (uSv/h)
status              processing status (completed, failed, ...)
client_track_id     identifier assigned by the uploading app
created_at          when the track was uploaded

measurements.csv
----------------
track_id            track this measurement belongs to
measurement_id      unique identifier of the measurement
dateTime            moment of the measurement (UTC)
timestamp           Unix timestamp, when provided by the device (often empty; use dateTime)
latitude, longitude WGS84 decimal degrees
altitude            metres
accuracy            GPS accuracy, metres
speed               speed at the time of the measurement
dose_rate           ambient dose-rate equivalent (see radiation_unit, usually uSv/h)
dose_rate_error     absolute error of dose_rate
dose_rate_rel_error relative error of dose_rate
radiation_unit      unit of dose_rate
cpm, cpm_error, cpm_rel_error   counts per minute and its errors
detector_type       detector model/type
calibration_factor  factor used to convert counts into dose rate
background_subtracted   whether background was subtracted
data_quality, capture_mode, notes   quality flag, capture mode (movement/static), free notes

spectra.csv
-----------
Each row is a gamma-ray energy spectrum accumulated over a segment of the track.
id, track_id, name, index   identifiers; index is the 0-based order within the track
started_at, ended_at        capture window (UTC)
duration_sec                integration (live) time, seconds
a0, a1, a2                  energy calibration coefficients
channel_count               number of channels (typically 1024)
start_lat, start_lon, start_alt, end_lat, end_lon   position at segment start/end
ch_0 ... ch_N               accumulated counts in each channel

Channel to energy:  E(keV) = a0 + a1*ch + a2*ch^2

Weather data and raw device payloads are not included in this export.
"""


def _val(v):
    return v.isoformat() if hasattr(v, 'isoformat') else ('' if v is None else v)


def build_tracks_zip(tracks_qs):
    """Return a seekable temp file positioned at 0 with the ZIP of the given tracks."""
    tracks = list(tracks_qs)
    ids = [t.id for t in tracks]
    tmp = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('README.txt', README)

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(TRACK_FIELDS)
        for t in tracks:
            w.writerow([_val(getattr(t, f)) for f in TRACK_FIELDS])
        zf.writestr('tracks.csv', buf.getvalue())

        with zf.open('measurements.csv', 'w') as raw:
            out = io.TextIOWrapper(raw, encoding='utf-8', newline='')
            w = csv.writer(out)
            w.writerow(MEASUREMENT_FIELDS)
            qs = (RadiationMeasurement.objects.filter(track_id__in=ids)
                  .order_by('track_id', 'dateTime').values_list(*MEASUREMENT_FIELDS))
            for row in qs.iterator(chunk_size=5000):
                w.writerow([_val(v) for v in row])
            out.flush()
            out.detach()

        spectra = list(Spectrum.objects.filter(track_id__in=ids).order_by('track_id', 'index'))
        if spectra:
            n = max(s.channel_count for s in spectra)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(SPECTRUM_META + [f'ch_{i}' for i in range(n)])
            for s in spectra:
                counts = list(s.counts or [])
                counts += [''] * (n - len(counts))
                w.writerow([_val(getattr(s, f)) for f in SPECTRUM_META] + counts)
            zf.writestr('spectra.csv', buf.getvalue())

        for t in tracks:
            if t.file:
                try:
                    with t.file.open('rb') as f:
                        zf.writestr(f'originals/track_{t.id}_{os.path.basename(t.file.name)}', f.read())
                except (FileNotFoundError, ValueError, OSError):
                    pass
    tmp.seek(0)
    return tmp
