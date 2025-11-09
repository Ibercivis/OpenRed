# Measurements API Reference

API endpoints for creating, reading, updating, and deleting measurement data.

## Overview

The Measurements API provides **full CRUD operations** for radiation and light pollution measurements.

**Base URL:** `/api/`

**Authentication:** Token-based (`Authorization: Token <your-token>`)

**Available Endpoints:**
- `/api/radiation-measurements/` - Gamma radiation data
- `/api/light-pollution-measurements/` - Light pollution data
- `/api/tracks/` - Batch CSV/GPX upload

---

## Radiation Measurements

### RadiationMeasurementViewSet

Full CRUD operations for gamma radiation measurements.

::: measures.views.RadiationMeasurementViewSet
    options:
      show_root_heading: true
      show_source: false
      members:
        - list
        - create
        - retrieve
        - update
        - destroy
      heading_level: 4

---

## Light Pollution Measurements

### LightPollutionMeasurementViewSet

Full CRUD operations for light pollution measurements.

::: measures.views.LightPollutionMeasurementViewSet
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

## Track Uploads

### TrackViewSet

Batch upload measurements from CSV/GPX files.

::: measures.views.TrackViewSet
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

## Models

### RadiationMeasurement

::: measures.models.RadiationMeasurement
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

### LightPollutionMeasurement

::: measures.models.LightPollutionMeasurement
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

### Track

::: measures.models.Track
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

## Serializers

### RadiationMeasurementSerializer

::: measures.serializers.RadiationMeasurementSerializer
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

### LightPollutionMeasurementSerializer

::: measures.serializers.LightPollutionMeasurementSerializer
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

### TrackSerializer

::: measures.serializers.TrackSerializer
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

## API Examples

### Create Single Radiation Measurement

```bash
curl -X POST https://api.openred.org/api/radiation-measurements/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project": 1,
    "device": 5,
    "latitude": 40.4168,
    "longitude": -3.7038,
    "altitude": 667.0,
    "cpm": 25,
    "usv_h": 0.125,
    "dateTime": "2024-11-20T14:30:00Z",
    "campaign": null
  }'
```

**Response (201 Created):**
```json
{
  "id": 12345,
  "project": 1,
  "device": 5,
  "campaign": null,
  "track": null,
  "latitude": 40.4168,
  "longitude": -3.7038,
  "altitude": 667.0,
  "cpm": 25,
  "usv_h": 0.125,
  "dateTime": "2024-11-20T14:30:00Z",
  "created_at": "2024-11-20T14:30:15Z",
  "h3_index": "8c1e9342b8b51ff"
}
```

---

### List Radiation Measurements (Filtered)

```bash
# Get measurements from specific project
curl -H "Authorization: Token YOUR_TOKEN" \
     "https://api.openred.org/api/radiation-measurements/?project=1"

# Filter by date range
curl -H "Authorization: Token YOUR_TOKEN" \
     "https://api.openred.org/api/radiation-measurements/?dateTime__gte=2024-01-01&dateTime__lte=2024-12-31"

# Filter by device
curl -H "Authorization: Token YOUR_TOKEN" \
     "https://api.openred.org/api/radiation-measurements/?device=5"

# Combine filters
curl -H "Authorization: Token YOUR_TOKEN" \
     "https://api.openred.org/api/radiation-measurements/?project=1&device=5&dateTime__gte=2024-11-01"
```

**Response:**
```json
{
  "count": 1523,
  "next": "https://api.openred.org/api/radiation-measurements/?page=2",
  "previous": null,
  "results": [
    {
      "id": 12345,
      "project": 1,
      "device": 5,
      "latitude": 40.4168,
      "longitude": -3.7038,
      "cpm": 25,
      "usv_h": 0.125,
      "dateTime": "2024-11-20T14:30:00Z"
    },
    // ... 99 more items (default page size: 100)
  ]
}
```

---

### Get Single Measurement

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
     https://api.openred.org/api/radiation-measurements/12345/
```

**Response:**
```json
{
  "id": 12345,
  "project": 1,
  "device": 5,
  "campaign": 3,
  "track": 15,
  "latitude": 40.4168,
  "longitude": -3.7038,
  "altitude": 667.0,
  "cpm": 25,
  "usv_h": 0.125,
  "cpm_error": null,
  "dateTime": "2024-11-20T14:30:00Z",
  "created_at": "2024-11-20T14:30:15Z",
  "updated_at": "2024-11-20T14:30:15Z",
  "h3_index": "8c1e9342b8b51ff"
}
```

---

### Update Measurement

```bash
curl -X PATCH https://api.openred.org/api/radiation-measurements/12345/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cpm": 26,
    "usv_h": 0.130
  }'
```

**Response (200 OK):**
```json
{
  "id": 12345,
  "project": 1,
  "device": 5,
  "latitude": 40.4168,
  "longitude": -3.7038,
  "cpm": 26,
  "usv_h": 0.130,
  "dateTime": "2024-11-20T14:30:00Z",
  "updated_at": "2024-11-20T15:45:22Z"
}
```

---

### Delete Measurement

```bash
curl -X DELETE https://api.openred.org/api/radiation-measurements/12345/ \
  -H "Authorization: Token YOUR_TOKEN"
```

**Response (204 No Content):**
```
(empty response body)
```

---

### Upload CSV Track

```bash
curl -X POST https://api.openred.org/api/tracks/upload/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -F "file=@measurements.csv" \
  -F "project=1" \
  -F "device=5" \
  -F "campaign=3"
```

**Response (202 Accepted):**
```json
{
  "id": 42,
  "file": "/media/tracks/2024/11/measurements.csv",
  "project": 1,
  "device": 5,
  "campaign": 3,
  "status": "pending",
  "created_at": "2024-11-20T15:00:00Z"
}
```

---

### Check Track Processing Status

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
     https://api.openred.org/api/tracks/42/status/
```

**Response (200 OK):**

**While processing:**
```json
{
  "id": 42,
  "status": "processing",
  "progress": "Processing row 523/1000"
}
```

**When complete:**
```json
{
  "id": 42,
  "status": "completed",
  "measurements_created": 1000,
  "processing_time": "2.3 seconds"
}
```

**If failed:**
```json
{
  "id": 42,
  "status": "failed",
  "error_message": "Invalid CSV format: missing 'latitude' column"
}
```

---

## CSV Upload Format

### Radiation Measurements CSV

**Required columns:**
- `latitude` - Decimal degrees (-90 to 90)
- `longitude` - Decimal degrees (-180 to 180)
- `dateTime` - ISO 8601 format (e.g., `2024-11-20T14:30:00Z`)
- `cpm` - Counts per minute (integer)

**Optional columns:**
- `altitude` - Meters above sea level
- `usv_h` - Microsieverts per hour
- `cpm_error` - Measurement error/uncertainty

**Example CSV:**
```csv
latitude,longitude,dateTime,cpm,usv_h,altitude
40.4168,-3.7038,2024-11-20T14:30:00Z,25,0.125,667
40.4170,-3.7040,2024-11-20T14:31:00Z,24,0.120,668
40.4172,-3.7042,2024-11-20T14:32:00Z,26,0.130,670
```

---

### Light Pollution Measurements CSV

**Required columns:**
- `latitude`
- `longitude`
- `dateTime`
- `mpsas` - Magnitudes per square arcsecond

**Optional columns:**
- `altitude`
- `nelm` - Naked Eye Limiting Magnitude

**Example CSV:**
```csv
latitude,longitude,dateTime,mpsas,nelm
40.4168,-3.7038,2024-11-20T22:30:00Z,18.5,4.2
40.4170,-3.7040,2024-11-20T22:35:00Z,18.7,4.3
```

---

## Query Parameters

### Filtering

**By Project:**
```bash
GET /api/radiation-measurements/?project=1
```

**By Device:**
```bash
GET /api/radiation-measurements/?device=5
```

**By Campaign:**
```bash
GET /api/radiation-measurements/?campaign=3
```

**By Date Range:**
```bash
# Greater than or equal
GET /api/radiation-measurements/?dateTime__gte=2024-01-01

# Less than or equal
GET /api/radiation-measurements/?dateTime__lte=2024-12-31

# Between dates
GET /api/radiation-measurements/?dateTime__gte=2024-01-01&dateTime__lte=2024-12-31
```

**By Geographic Bounds:**
```bash
# Latitude range
GET /api/radiation-measurements/?latitude__gte=40.0&latitude__lte=41.0

# Longitude range
GET /api/radiation-measurements/?longitude__gte=-4.0&longitude__lte=-3.0
```

**By Measurement Value:**
```bash
# CPM greater than threshold
GET /api/radiation-measurements/?cpm__gte=50

# CPM range
GET /api/radiation-measurements/?cpm__gte=20&cpm__lte=100
```

---

### Pagination

```bash
# Default: 100 items per page
GET /api/radiation-measurements/

# Custom page size (max 1000)
GET /api/radiation-measurements/?page_size=500

# Navigate pages
GET /api/radiation-measurements/?page=2
```

---

### Ordering

```bash
# Order by date (newest first)
GET /api/radiation-measurements/?ordering=-dateTime

# Order by date (oldest first)
GET /api/radiation-measurements/?ordering=dateTime

# Order by CPM (highest first)
GET /api/radiation-measurements/?ordering=-cpm
```

---

### Search

```bash
# Search by project name, device name, etc.
GET /api/radiation-measurements/?search=barcelona
```

---

## Permissions

### Create Measurements

**Requirements:**
- Must be authenticated (valid token)
- Must be a member of the project (or project is public)
- Device must be owned by user or project allows any device

**Permission denied (403):**
```json
{
  "detail": "You do not have permission to create measurements in this project."
}
```

---

### Read Measurements

**Public projects:**
- Any authenticated user can read

**Private projects:**
- Only project members can read

---

### Update/Delete Measurements

**Requirements:**
- Must be authenticated
- Must be the creator of the measurement (`created_by`)
- OR be the project owner

---

## Status Codes

| Code | Meaning | Usage |
|------|---------|-------|
| 200 | OK | Successful GET/PUT/PATCH |
| 201 | Created | Successful POST (measurement created) |
| 202 | Accepted | Track upload accepted (processing in background) |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Invalid data (validation error) |
| 401 | Unauthorized | Missing or invalid token |
| 403 | Forbidden | No permission to access resource |
| 404 | Not Found | Measurement/Track doesn't exist |
| 413 | Payload Too Large | File upload too large |
| 422 | Unprocessable Entity | Semantic validation error |
| 500 | Internal Server Error | Server error (check logs) |

---

## Validation Errors

### Common Validation Errors

**Missing required field:**
```json
{
  "project": ["This field is required."],
  "device": ["This field is required."]
}
```

**Invalid coordinate:**
```json
{
  "latitude": ["Ensure this value is greater than or equal to -90."],
  "longitude": ["Ensure this value is less than or equal to 180."]
}
```

**Invalid date format:**
```json
{
  "dateTime": ["Datetime has wrong format. Use ISO 8601 format."]
}
```

**Project type mismatch:**
```json
{
  "project": ["Cannot create light pollution measurements in radiation project."]
}
```

---

## Performance Tips

### Batch Operations

**Instead of 1000 individual POST requests:**
```bash
# BAD - 1000 API calls
for measurement in measurements:
    curl -X POST /api/radiation-measurements/ -d $measurement
```

**Use CSV upload (1 request):**
```bash
# GOOD - 1 API call
curl -X POST /api/tracks/upload/ -F "file=@measurements.csv"
```

---

### Pagination

**Don't fetch all data at once:**
```bash
# BAD - May time out with millions of records
GET /api/radiation-measurements/?page_size=1000000

# GOOD - Use pagination
GET /api/radiation-measurements/?page_size=1000&page=1
GET /api/radiation-measurements/?page_size=1000&page=2
```

---

### Filtering

**Filter on server, not client:**
```bash
# BAD - Fetch all, filter in code
data = fetch('/api/radiation-measurements/')
filtered = data.filter(d => d.project === 1)

# GOOD - Filter on server
data = fetch('/api/radiation-measurements/?project=1')
```

---

## Rate Limiting

**Current limits:**
- 1000 requests per hour per user
- 10 MB max file size for CSV uploads
- 1000 max page size

**Rate limit headers:**
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 847
X-RateLimit-Reset: 1700492400
```

**Rate limit exceeded (429):**
```json
{
  "detail": "Request was throttled. Expected available in 3600 seconds."
}
```

---

## Related Documentation

- **[Track Upload Guide](../guides/uploading-tracks.md)** - Complete CSV upload tutorial
- **[DateTime Fields](../architecture/datetime-fields.md)** - Understanding time zones
- **[Data Hierarchy](../architecture/data-hierarchy.md)** - Project/Mission/Campaign structure
- **[Background Jobs](../architecture/background-jobs.md)** - How track processing works
- **[Quick Start Guide](../getting-started/quick-start.md)** - Complete API tutorial
