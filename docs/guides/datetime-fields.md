# DateTime Fields Reference

Understanding date and time fields in OpenRed models.

## Overview

OpenRed uses multiple datetime fields to track different aspects of measurements and data:

- **Measurement time** (`dateTime`): When the physical measurement was taken
- **Database time** (`created_at`): When the record was saved to the database
- **Track time range** (`start_time`/`end_time`): Temporal span of measurements in a file

This separation is crucial because measurements can be uploaded hours, days, or even months after they were captured by the sensor.

---

## BaseMeasurement Fields

Base class for `RadiationMeasurement` and `LightPollutionMeasurement`.

### Measurement Timestamp

| Field | Type | Purpose | Source |
|-------|------|---------|--------|
| **`dateTime`** | DateTimeField | ⭐ **WHEN the measurement was taken** (sensor data) | CSV file, device, manual entry |
| **`timestamp`** | BigIntegerField | Unix timestamp (numeric copy of `dateTime`) | Auto-calculated from `dateTime` |

**Usage:**
- `dateTime`: The actual physical measurement time from the sensor
- `timestamp`: Numeric version for calculations and APIs requiring Unix time

**Example:**
```python
measurement.dateTime = datetime(2024, 11, 20, 14, 30, 0, tzinfo=UTC)
measurement.timestamp = 1700492400  # Auto-calculated
```

---

### Database Audit Fields

| Field | Type | Purpose | Source |
|-------|------|---------|--------|
| **`created_at`** | DateTimeField (auto_now_add) | **When the record was CREATED** in database | Django (automatic) |

**Usage:**
- `created_at`: When the record was saved to the database (may be days/months after `dateTime`)

**Example:**
```python
# Measurement taken on Nov 20, 2024
measurement.dateTime = datetime(2024, 11, 20, 14, 30)

# But uploaded to database on Nov 3, 2025
measurement.created_at = datetime(2025, 11, 3, 10, 15)
```

---

### Weather Data Fields

| Field | Type | Purpose | Source |
|-------|------|---------|--------|
| **`weather_last_attempt`** | DateTimeField | When weather data was last fetched | System (OpenWeather API call) |

**Usage:**
- `weather_last_attempt`: Controls retry logic for weather data fetching

---

## Track Model Fields

Represents a file (CSV/GPX) containing multiple measurements.

### Content Temporal Range

| Field | Type | Purpose | Source |
|-------|------|---------|--------|
| **`start_time`** | DateTimeField | **First measurement** in track (minimum `dateTime`) | Calculated from measurements |
| **`end_time`** | DateTimeField | **Last measurement** in track (maximum `dateTime`) | Calculated from measurements |

**Usage:**
- `start_time`: When data collection started in the track
- `end_time`: When data collection ended in the track
- Represents the **temporal range of measurements**, NOT when the file was uploaded

**Example:**
```python
# Track contains measurements from Nov 3, 2024, 10:00 to 10:09
track.start_time = datetime(2024, 11, 3, 10, 0, 0)
track.end_time = datetime(2024, 11, 3, 10, 9, 0)

# But the file was uploaded on Nov 3, 2025
track.created_at = datetime(2025, 11, 3, 14, 30, 0)
```

---

### Database Audit Fields

| Field | Type | Purpose | Source |
|-------|------|---------|--------|
| **`created_at`** | DateTimeField (auto_now_add) | When file was UPLOADED to system | Django (automatic) |
| **`updated_at`** | DateTimeField (auto_now) | Last modification of Track record | Django (automatic) |

**Usage:**
- `created_at`: Timestamp of file upload
- `updated_at`: Last time track status was modified (e.g., pending → completed)

---

## Project Model Fields

| Field | Type | Purpose |
|-------|------|---------|
| **`created_at`** | DateTimeField (auto_now_add) | When project was created |
| **`updated_at`** | DateTimeField (auto_now) | Last project modification |

---

## Common Scenarios

### Scenario 1: Upload Track with Old Measurements

```
Track uploaded: 2025-11-03 14:30:00
Track.created_at = 2025-11-03 14:30:00  ← When file was uploaded
Track.start_time = 2024-11-03 10:00:00  ← First measurement in CSV
Track.end_time   = 2024-11-03 10:09:00  ← Last measurement in CSV

Measurements:
  dateTime    = 2024-11-03 10:00:00  ← When measurement was TAKEN
  created_at  = 2025-11-03 14:30:00  ← When SAVED to database
```

**Key point:** The measurement was taken 1 year ago, but uploaded today.

---

### Scenario 2: Real-time Measurement (from device)

```
Measurement taken: 2025-11-03 15:00:00
Immediately uploaded: 2025-11-03 15:00:05

Measurement:
  dateTime    = 2025-11-03 15:00:00  ← Measurement time
  created_at  = 2025-11-03 15:00:05  ← Database save time (5 sec later)
  track       = None                  ← No track (direct API)
```

---

### Scenario 3: CSV with Future Timestamps (Error)

```
Upload time: 2025-11-03 14:00:00
CSV contains: 2025-11-04 10:00:00  ← FUTURE date

Result: ⚠️ Warning logged, but measurement created
Reason: Sensor clocks may be misconfigured
```

**Best practice:** Validate `dateTime <= now()` before upload.

---

## Timezone Handling

### Aware vs Naive Datetimes

**Aware datetime** (has timezone info):
```python
datetime(2024, 11, 20, 14, 30, 0, tzinfo=UTC)  ✅
```

**Naive datetime** (no timezone):
```python
datetime(2024, 11, 20, 14, 30, 0)  ⚠️
```

### OpenRed Behavior

1. **CSV Upload:**
   - If timestamp has timezone → Use it
   - If timestamp is naive → Convert to **Django's default timezone** (settings.TIME_ZONE)

2. **API POST:**
   - ISO 8601 with `Z` suffix → UTC
   - ISO 8601 with offset → Use offset
   - No timezone → Interpreted as default timezone

3. **Database Storage:**
   - Always stored as UTC in PostgreSQL
   - Displayed in user's timezone via Django settings

### Example CSV Timestamps

```csv
# With timezone (recommended)
dateTime
2024-11-20T14:30:00Z           # UTC
2024-11-20T14:30:00+01:00      # CET (Central European Time)
2024-11-20T14:30:00-05:00      # EST (Eastern Standard Time)

# Without timezone (uses default timezone)
2024-11-20T14:30:00            # Interpreted as TIME_ZONE setting
```

### Code Example: Making Naive Aware

```python
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# Parse timestamp string
dt = parse_datetime('2024-11-20T14:30:00')

# Check if naive
if timezone.is_naive(dt):
    # Make aware using default timezone
    dt = timezone.make_aware(dt)

# Now it's safe to save
measurement.dateTime = dt
measurement.save()
```

---

## Querying by DateTime

### Filter by Date Range

```python
from django.utils import timezone
from datetime import timedelta

# Last 7 days
seven_days_ago = timezone.now() - timedelta(days=7)
measurements = RadiationMeasurement.objects.filter(
    dateTime__gte=seven_days_ago
)

# Specific date range
from_date = timezone.make_aware(datetime(2024, 1, 1))
to_date = timezone.make_aware(datetime(2024, 12, 31))
measurements = RadiationMeasurement.objects.filter(
    dateTime__gte=from_date,
    dateTime__lte=to_date
)
```

### API Query Examples

```bash
# Measurements from last 7 days
GET /api/radiation-measurements/?dateTime__gte=2024-11-13T00:00:00Z

# Specific date range
GET /api/radiation-measurements/?dateTime__gte=2024-01-01&dateTime__lte=2024-12-31

# Measurements uploaded today (created_at)
GET /api/radiation-measurements/?created_at__gte=2025-11-03T00:00:00Z
```

---

## Best Practices

### ✅ DO

- **Always use timezone-aware datetimes** in CSV files
- **Include timezone suffix** (`Z` or `+HH:MM`) in timestamps
- **Validate dateTime <= now()** to catch sensor clock errors
- **Use `dateTime`** for scientific queries (when was measured)
- **Use `created_at`** for audit/admin queries (when was uploaded)

### ❌ DON'T

- Don't assume user's local timezone without explicit configuration
- Don't mix naive and aware datetimes in the same codebase
- Don't use `created_at` for scientific analysis (it's when uploaded, not measured)
- Don't forget to set Django's `TIME_ZONE` and `USE_TZ = True`

---

## Django Settings

Ensure these settings in `settings.py`:

```python
# Enable timezone support
USE_TZ = True

# Set your default timezone
TIME_ZONE = 'UTC'  # Or 'Europe/Madrid', 'America/New_York', etc.

# Database timezone (PostgreSQL)
DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'TIME_ZONE': 'UTC',  # Always UTC in database
        ...
    }
}
```

---

## Related Documentation

- **[Track Upload Guide](uploading-tracks.md)** - CSV upload with timestamps
- **[Data Hierarchy](../architecture/data-hierarchy.md)** - Track and measurement relationships
- **[API Reference](../api-reference/measures.md)** - DateTime filtering in API
