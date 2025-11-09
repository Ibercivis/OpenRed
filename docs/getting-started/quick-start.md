# Quick Start Guide

Get up and running with OpenRed in 5 minutes. This guide assumes you've completed the [Installation](installation.md).

## 1. Create a Project

Projects define the type of measurements you're collecting (radiation or light pollution).

### Via Django Admin

1. Go to `http://localhost:8000/admin/missions/project/`
2. Click **"Add Project"**
3. Fill in the form:
   - **Name**: "My Radiation Project"
   - **Project Type**: Select "Radiación Gamma"
   - **Is Active**: ✓ (checked)
   - **Is Public**: ✓ (checked for public access)
4. Click **"Save"**

### Via Django Shell

```python
python manage.py shell
```

```python
from missions.models import Project
from django.contrib.auth.models import User

# Get or create a user
user = User.objects.first()

# Create a radiation project
project = Project.objects.create(
    name="My Radiation Project",
    project_type="radiation",
    is_active=True,
    is_public=True,
    created_by=user
)

print(f"Created project ID: {project.id}")
```

## 2. Register a Device

Devices represent physical measurement hardware (radiation detectors, sky quality meters, etc.).

### Via Admin

1. Go to `http://localhost:8000/admin/devices/device/`
2. Click **"Add Device"**
3. Fill in:
   - **Name**: "GeigerCounter-01"
   - **Device Type**: Select appropriate type
   - **Owner**: Select your user
4. Save

### Via Shell

```python
from devices.models import Device
from django.contrib.auth.models import User

user = User.objects.first()

device = Device.objects.create(
    name="GeigerCounter-01",
    device_type="geiger_counter",
    owner=user
)

print(f"Created device ID: {device.id}")
```

## 3. Get Authentication Token

### Via API (Registration)

```bash
curl -X POST http://localhost:8000/dj-rest-auth/registration/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "myemail@example.com",
    "password1": "SecurePass123!",
    "password2": "SecurePass123!"
  }'
```

**Response:**
```json
{
  "key": "abc123def456token789"
}
```

### Via Django Admin

1. Go to `http://localhost:8000/admin/authtoken/token/`
2. Click **"Add Token"**
3. Select your user
4. Save and copy the token

!!! warning "Save Your Token"
    Store your authentication token securely. You'll need it for all API requests.

## 4. Upload Your First Measurement

Now that you have a project, device, and token, let's upload data!

### Option A: Single Measurement via API

```bash
TOKEN="your_token_here"

curl -X POST http://localhost:8000/api/radiation-measurements/ \
  -H "Authorization: Token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project": 1,
    "device": 1,
    "latitude": 40.4168,
    "longitude": -3.7038,
    "altitude": 650,
    "cpm": 100,
    "acpm": 98.5,
    "usv_h": 0.50,
    "dateTime": "2024-11-03T10:00:00Z"
  }'
```

**Response:**
```json
{
  "id": 1,
  "measurement_id": "550e8400-e29b-41d4-a716-446655440000",
  "device": 1,
  "user": 1,
  "project": 1,
  "latitude": "40.41680000",
  "longitude": "-3.70380000",
  "altitude": 650.0,
  "cpm": 100,
  "acpm": 98.5,
  "usv_h": 0.5,
  "dateTime": "2024-11-03T10:00:00Z",
  "created_at": "2024-11-03T10:05:23.123456Z"
}
```

### Option B: Upload CSV File (Batch Import)

Create a file called `measurements.csv`:

```csv
timestamp,latitude,longitude,altitude,cpm,acpm,radiation_value
2024-11-03T10:00:00,40.4168,-3.7038,650,100,98.5,0.50
2024-11-03T10:01:00,40.4169,-3.7039,651,105,102.3,0.52
2024-11-03T10:02:00,40.4170,-3.7040,652,98,96.1,0.49
2024-11-03T10:03:00,40.4171,-3.7041,653,103,100.2,0.51
```

Upload the file:

```bash
curl -X POST http://localhost:8000/api/tracks/upload/ \
  -H "Authorization: Token $TOKEN" \
  -F "file=@measurements.csv" \
  -F "device=1" \
  -F "project=1" \
  -F "description=Test upload from quick start guide"
```

**Response:**
```json
{
  "track_id": 1,
  "job_id": "track_1",
  "status": "pending",
  "message": "Track file uploaded successfully. Processing in background."
}
```

!!! success "Asynchronous Processing"
    The CSV file is being processed in the background by RQ worker. Large files won't block the API.

## 5. Check Processing Status

```bash
curl http://localhost:8000/api/tracks/1/status/ \
  -H "Authorization: Token $TOKEN"
```

**While processing:**
```json
{
  "track_id": 1,
  "status": "processing",
  "processed_measurements": 2,
  "total_measurements": 4
}
```

**When complete:**
```json
{
  "track_id": 1,
  "status": "completed",
  "measurements_count": 4,
  "start_time": "2024-11-03T10:00:00Z",
  "end_time": "2024-11-03T10:03:00Z"
}
```

## 6. Query Your Data

Now let's retrieve the measurements you've uploaded.

### Get All Measurements

```bash
curl http://localhost:8000/api/radiation-measurements/
```

### Filter by Project

```bash
curl http://localhost:8000/api/radiation-measurements/?project=1
```

### Filter by Track

```bash
curl http://localhost:8000/api/radiation-measurements/?track=1
```

### Filter by Date Range

```bash
curl "http://localhost:8000/api/radiation-measurements/?dateTime__gte=2024-11-03T10:00:00&dateTime__lte=2024-11-03T10:05:00"
```

### Filter by Location (Bounding Box)

```bash
curl "http://localhost:8000/api/radiation-measurements/?latitude__gte=40.4&latitude__lte=40.5&longitude__gte=-3.8&longitude__lte=-3.7"
```

## 7. View in Admin Panel

1. Go to `http://localhost:8000/admin/`
2. Navigate to **Measures** → **Radiation measurements**
3. See your uploaded data with filtering options

## 8. Explore Swagger UI

1. Go to `http://localhost:8000/swagger/`
2. Click **"Authorize"** and enter your token
3. Try out different endpoints interactively

## What's Next?

Congratulations! You've successfully:

- ✅ Created a project
- ✅ Registered a device
- ✅ Uploaded measurements (single and batch)
- ✅ Queried your data

### Continue Learning

- 📖 **[Data Hierarchy](../architecture/data-hierarchy.md)** - Understand Projects, Missions, and Campaigns
- 📁 **[Uploading Tracks](../guides/uploading-tracks.md)** - Complete guide to CSV/GPX uploads
- 🔐 **[Permissions Guide](../guides/permissions.md)** - Control who can access your data
- 🔍 **[API Reference](../api-reference/missions.md)** - Explore all available endpoints

### Advanced Topics

- Create **Missions** and **Campaigns** to organize field work
- Set up **weather data fetching** with OpenWeather API
- Configure **email notifications** for processing completion
- Deploy to **production** with Docker

---

Need help? Check the **[Troubleshooting Guide](../reference/troubleshooting.md)** or open an issue on GitHub.
