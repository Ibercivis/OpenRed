# Track Upload Feature - Documentation

## Overview

The Track Upload feature allows users to upload CSV or GPX files containing multiple measurements. The system **processes files asynchronously using RQ (Redis Queue)** to avoid blocking HTTP requests, making it suitable for large files with thousands of measurements.

### Key Features
- ✅ Asynchronous processing with RQ (Redis Queue)
- ✅ Support for CSV files (GPX and JSON planned)
- ✅ Automatic timezone handling for naive datetimes
- ✅ Bulk creation of measurements (batch_size=1000)
- ✅ Status tracking (pending/processing/completed/failed)
- ✅ Optional campaign association
- ✅ Automatic track naming from filename
- ✅ All measurements linked to track via FK

### What is a Track?

A **Track** represents an **uploaded file** (CSV/GPX/JSON) containing multiple measurement data points. When you upload a track:

1. Track record is created in the database with `status='pending'`
2. File is saved to disk (`media/tracks/YYYY/MM/DD/`)
3. RQ background job processes the file
4. Each row in the CSV becomes a **RadiationMeasurement** with `track` FK set
5. Track status changes to `'completed'` or `'failed'`

**Key distinction:**
- **Track** = File container + metadata
- **Measurements** = Individual data points (can come from track OR direct API)

### Track vs Direct Measurements

| Creation Method | Track FK | Use Case |
|----------------|----------|----------|
| **CSV Upload** | ✅ Set | Batch import from file |
| **API POST** | ❌ None | Single measurement creation |

```python
# Measurements from track
measurement.track = Track object  # Imported from CSV

# Direct measurement (no track)
measurement.track = None  # Created via API POST
```

### Data Hierarchy

Tracks can exist at different organizational levels:

```
Project (required)
  └─ Mission (optional)
       └─ Campaign (optional)
            └─ Track
                 └─ Measurements (track FK set)
```

**Track with campaign (organized data):**
```python
track.project     # Required - Project ID
track.campaign    # Optional - Campaign ID
track.mission     # Property - Derived from campaign.mission
track.name        # Property - Filename (read-only)
```

**Track without campaign (loose project data):**
```python
track.project     # Required - Project ID
track.campaign    # None
track.mission     # None
track.name        # Property - Filename (read-only)
```

See `HIERARCHY.md` for complete data organization details.

---

## API Endpoints

### 1. Upload Track File (Async)

**Endpoint:** `POST /api/tracks/upload/`

**Authentication:** Required (Token)

**Content-Type:** `multipart/form-data`

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | File | Yes | CSV or GPX file with measurements |
| `device` | Integer | Yes | Device ID that captured the data |
| `project` | Integer | Yes | Project ID (radiation/light_pollution) |
| `campaign` | Integer | No | Optional campaign ID |

**Response (Success - 202 ACCEPTED):**

```json
{
    "track_id": 5,
    "job_id": "track_5",
    "status": "pending",
    "message": "Track file uploaded successfully. Processing in background.",
    "status_url": "/api/tracks/5/status/"
}
```

**Notes:**
- Returns **202 ACCEPTED** (not 201) since processing is async
- Track starts with `status='pending'`
- Background worker processes the file
- Use `status_url` to check progress

**Response (Error - 400):**

```json
{
    "error": "Device with id 999 not found"
}
```

### 2. Check Track Status

**Endpoint:** `GET /api/tracks/{id}/status/`

**Authentication:** Required (Token)

**Response:**

```json
{
    "track_id": 5,
    "status": "completed",
    "measurements_count": 150,
    "error_message": null,
    "start_time": "2024-11-03T10:00:00Z",
    "end_time": "2024-11-03T10:09:00Z"
}
```

**Status values:**
- `pending` - Waiting for processing
- `processing` - Currently being processed
- `completed` - Successfully processed
- `failed` - Processing failed (see `error_message`)

## CSV Format

### Expected Format

```csv
timestamp,latitude,longitude,cpm,radiation_value
2024-11-03T10:00:00,40.4168,-3.7038,100,0.50
2024-11-03T10:01:00,40.4169,-3.7039,105,0.52
```

### Required Columns

- `timestamp` or `dateTime` or `datetime` - ISO 8601 format
- `latitude` - Decimal degrees
- `longitude` - Decimal degrees
- `radiation_value` - Float (radiation measurement value)

### Optional Columns

- `cpm` - Integer (counts per minute)
- `altitude` - Float (meters)
- `accuracy` - Float (GPS accuracy in meters)

## Usage Examples

### Using cURL

```bash
# Get your authentication token first
TOKEN="your_auth_token_here"

# Upload track file
curl -X POST http://192.168.1.2:8001/api/tracks/upload/ \
  -H "Authorization: Token $TOKEN" \
  -F "file=@example_track.csv" \
  -F "device=1" \
  -F "project=1" \
  -F "campaign=1"
```

### Using Python requests

```python
import requests

# Authentication
token = "your_auth_token_here"
headers = {"Authorization": f"Token {token}"}

# Prepare file and data
files = {"file": open("example_track.csv", "rb")}
data = {
    "device": 1,
    "project": 1,
    "campaign": 1,  # optional
}

# Upload track
response = requests.post(
    "http://192.168.1.2:8001/api/tracks/upload/",
    headers=headers,
    files=files,
    data=data
)

print(response.json())
```

### Using JavaScript/Fetch

```javascript
// Get your auth token
const token = "your_auth_token_here";

// Prepare form data
const formData = new FormData();
formData.append("file", fileInput.files[0]);
formData.append("device", "1");
formData.append("project", "1");
formData.append("campaign", "1");  // optional

// Upload track
fetch("http://192.168.1.2:8001/api/tracks/upload/", {
    method: "POST",
    headers: {
        "Authorization": `Token ${token}`
    },
    body: formData
})
.then(response => response.json())
.then(data => console.log(data))
.catch(error => console.error("Error:", error));
```

## Track Model Fields

### Database Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | Integer | Primary key |
| `name` | String (Property) | **Read-only** - Automatically derived from filename |
| `project` | ForeignKey | Associated project (required) |
| `device` | ForeignKey | Device used for measurements (required) |
| `campaign` | ForeignKey | Campaign (optional, can be None) |
| `file` | FileField | Uploaded file path |
| `file_type` | String | File type (csv, gpx, json) |
| `description` | Text | Track description (optional) |
| `start_time` | DateTime | First measurement timestamp (auto-calculated) |
| `end_time` | DateTime | Last measurement timestamp (auto-calculated) |
| `total_measurements` | Integer | Number of measurements created |
| `status` | String | Processing status (pending, processing, completed, failed) |
| `error_message` | Text | Error details if failed |
| `created_at` | DateTime | Track creation timestamp (auto) |
| `created_by` | ForeignKey | User who uploaded the track |
| `updated_at` | DateTime | Last update timestamp (auto) |

### Properties (Read-Only)

- `name`: Automatically set from the filename (e.g., `"example_track.csv"`)
- `mission`: Derived from `campaign.mission` (None if no campaign)

**Note:** The `name` field is **not writable** - it's always derived from the uploaded filename.

## Permissions

### Track Endpoints

| Action | Anonymous | Authenticated (Owner) | Authenticated (Other) |
|--------|-----------|----------------------|----------------------|
| **GET list** | ✅ Public projects only | ✅ Public + own tracks | ✅ Public projects only |
| **GET retrieve** | ✅ If project public | ✅ If public or own | ✅ If project public |
| **POST create** | ❌ 401 | ✅ Creates track | ✅ Creates track |
| **POST upload** | ❌ 401 | ✅ Uploads file | ✅ Uploads file |
| **PUT/PATCH** | ❌ 401 | ✅ Own tracks only | ❌ 404 |
| **DELETE** | ❌ 401 | ✅ Own tracks only | ❌ 404 |

### Measurements from Tracks

All measurements created from a track:
- Are automatically linked to the track (via `track` ForeignKey)
- Belong to the user who uploaded the track
- Follow the same permissions as regular measurements
- Can be viewed publicly (GET)
- Can only be modified/deleted by the owner

## Retrieving Track Measurements

### Get all measurements from a track

```bash
# Using track_id filter
curl http://192.168.1.2:8001/api/radiation-measurements/?track=1
```

### Python example

```python
import requests

# Get track details
track_id = 1
response = requests.get(f"http://192.168.1.2:8001/api/tracks/{track_id}/")
track = response.json()

print(f"Track: {track['name']}")
print(f"Measurements: {track['measurements_count']}")
print(f"Time range: {track['start_time']} to {track['end_time']}")

# Get all measurements from this track
response = requests.get(
    f"http://192.168.1.2:8001/api/radiation-measurements/?track={track_id}"
)
measurements = response.json()
```

## Error Handling

### Common Errors

| Error Code | Reason | Solution |
|------------|--------|----------|
| 400 | No file provided | Include `file` in the request |
| 400 | No device ID | Include `device` parameter |
| 400 | No project ID | Include `project` parameter |
| 400 | Invalid file format | Check CSV structure |
| 400 | No valid measurements | Verify CSV has data rows |
| 401 | Not authenticated | Include valid auth token |
| 404 | Device/Project not found | Verify IDs exist |

### Track Status Values

- **pending**: Track created, not yet processed
- **processing**: File is being parsed
- **completed**: All measurements created successfully
- **failed**: Error occurred (check `error_message` field)

## Example Workflow

1. **Register/Login** to get auth token
2. **Create or select** a device and project
3. **Prepare CSV file** with measurements
4. **Upload track** using `/api/tracks/upload/` endpoint
5. **Check track status** via `/api/tracks/{id}/`
6. **View measurements** via `/api/radiation-measurements/?track={id}`

## Notes

- Files are stored in `media/tracks/YYYY/MM/DD/` directory
- Large files may take time to process
- Bulk insert is used for efficiency
- Track's `start_time` and `end_time` are auto-calculated from measurements
- GPX format support is planned but not yet implemented
