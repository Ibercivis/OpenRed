# OpenRed Data Hierarchy

This document describes the organizational hierarchy of data in OpenRed.

## Overview

OpenRed uses a flexible hierarchy that allows data to be organized at different levels depending on the organizational needs.

```
Project (required)
  ├─ Mission (optional)
  │    └─ Campaign (optional)
  │         ├─ Track
  │         └─ Measurement
  ├─ Track (without campaign)
  └─ Measurement (without campaign)
```

## Models

### 1. Project (`measures.Project`)

**Purpose:** Defines the type of measurement system (radiation or light pollution)

**Attributes:**
- `name`: Project name
- `project_type`: Type of project (`radiation` or `light_pollution`)
- `is_public`: Whether data is publicly accessible
- `is_active`: Whether project is active
- `project_settings`: JSON field for flexible configuration

**Required:** YES - All data must belong to a project

**Example:**
```python
project = Project.objects.create(
    name="Gamma Radiation Monitoring",
    project_type="radiation",
    is_public=True
)
```

---

### 2. Mission (`missions.Mission`)

**Purpose:** Abstract organizational grouping of campaigns

**Attributes:**
- `name`: Mission name (unique within project)
- `description`: Mission objectives and description
- `project`: Parent project (FK, required)
- `start_date`: Mission start date
- `end_date`: Mission end date
- `created_by`: User who created the mission

**Required:** NO - Data can exist without a mission

**Example:**
```python
mission = Mission.objects.create(
    name="Autumn 2024 Campaign",
    project=project,
    start_date=date(2024, 9, 1),
    end_date=date(2024, 11, 30)
)
```

**Relationships:**
- `project.missions` - All missions in a project
- `mission.campaigns` - All campaigns in a mission

---

### 3. Campaign (`missions.Campaign`)

**Purpose:** Specific data collection effort with defined participants and devices

**Attributes:**
- `name`: Campaign name (unique within mission)
- `description`: Campaign description
- `mission`: Parent mission (FK, required)
- `participants`: Users participating (M2M)
- `devices`: Devices used (M2M)
- `start_date`: Campaign start date
- `end_date`: Campaign end date
- `created_by`: User who created the campaign

**Required:** NO - Data can exist without a campaign

**Properties:**
- `campaign.project` - Direct access to parent project through mission

**Example:**
```python
campaign = Campaign.objects.create(
    name="Barcelona Field Study",
    mission=mission,
    start_date=date(2024, 9, 15),
    end_date=date(2024, 9, 20)
)
campaign.participants.add(user1, user2)
campaign.devices.add(device1)
```

**Relationships:**
- `mission.campaigns` - All campaigns in a mission
- `campaign.tracks` - All tracks in a campaign
- `campaign.radiationmeasurement_set` - All radiation measurements in campaign
- `campaign.lightpollutionmeasurement_set` - All light pollution measurements in campaign

---

### 4. Track (`measures.Track`)

**Purpose:** Represents an uploaded file (CSV/GPX) containing multiple measurements

**Attributes:**
- `project`: Parent project (FK, required)
- `campaign`: Optional campaign (FK, optional)
- `device`: Device that captured measurements (FK, required)
- `file`: Uploaded file (CSV/GPX/JSON)
- `file_type`: Type of file
- `status`: Processing status (pending/processing/completed/failed)
- `start_time`: First measurement timestamp (auto-calculated)
- `end_time`: Last measurement timestamp (auto-calculated)
- `total_measurements`: Count of measurements (auto-calculated)
- `created_by`: User who uploaded the track

**Required:** NO - Measurements can exist without a track

**Properties:**
- `track.name` - Filename (read-only)
- `track.mission` - Parent mission through campaign (if campaign exists)

**Example:**
```python
# Track with campaign
track = Track.objects.create(
    project=project,
    campaign=campaign,  # Optional
    device=device,
    file=uploaded_file,
    file_type='csv',
    status='pending'
)

# Track without campaign (loose data)
track = Track.objects.create(
    project=project,
    campaign=None,  # No campaign
    device=device,
    file=uploaded_file,
    file_type='csv'
)
```

**Processing Flow:**
1. Track created with `status='pending'`
2. RQ job enqueued: `process_track_file(track_id)`
3. Status changes to `'processing'`
4. CSV parsed, measurements bulk-created
5. Status changes to `'completed'` or `'failed'`

**Relationships:**
- `project.tracks` - All tracks in project
- `campaign.tracks` - All tracks in campaign (if any)
- `track.radiationmeasurement_set` - All radiation measurements from track
- `track.lightpollutionmeasurement_set` - All light pollution measurements from track

---

### 5. Measurement (`measures.BaseMeasurement`)

**Purpose:** Individual measurement data point

**Subtypes:**
- `RadiationMeasurement` - Gamma radiation measurements
- `LightPollutionMeasurement` - Light pollution measurements

**Attributes:**
- `project`: Parent project (FK, required)
- `campaign`: Optional campaign (FK, optional)
- `track`: Optional track (FK, optional)
- `device`: Device that captured measurement (FK, required)
- `user`: User who recorded measurement (FK, optional)
- `dateTime`: When measurement was taken (from sensor)
- `latitude`: Measurement location latitude
- `longitude`: Measurement location longitude
- `altitude`: Measurement altitude (optional)
- Weather data (temperature, humidity, pressure, etc.)

**Required:** YES - But campaign and track are optional

**Properties:**
- `measurement.mission` - Parent mission through campaign (if campaign exists)

**Example:**
```python
# Measurement in campaign (organized)
measurement = RadiationMeasurement.objects.create(
    project=project,
    campaign=campaign,  # Optional
    track=track,        # Optional
    device=device,
    latitude=40.4168,
    longitude=-3.7038,
    dateTime=timezone.now(),
    radiation_value=0.15
)

# Measurement without campaign (loose data)
measurement = RadiationMeasurement.objects.create(
    project=project,
    campaign=None,  # No campaign
    track=None,     # No track
    device=device,
    latitude=40.4168,
    longitude=-3.7038,
    dateTime=timezone.now(),
    radiation_value=0.15
)
```

---

## Use Cases

### Case 1: Fully Organized Data
Data is part of a mission and campaign with multiple participants and devices.

```python
project = Project.objects.get(project_type='radiation')
mission = Mission.objects.create(name="Research 2024", project=project, ...)
campaign = Campaign.objects.create(name="Field Study", mission=mission, ...)
track = Track.objects.create(project=project, campaign=campaign, ...)
# Measurements automatically linked to project, campaign, and track
```

**Hierarchy:**
```
Project → Mission → Campaign → Track → Measurements
```

---

### Case 2: Campaign Data (Without Mission)
Data is part of a specific campaign but not a broader mission.

```python
project = Project.objects.get(project_type='light_pollution')
# No mission created
campaign = Campaign.objects.create(name="Quick Survey", mission=None, ...)  # ❌ Campaign requires mission
```

**Note:** Campaigns ALWAYS require a mission. Use Case 3 instead.

---

### Case 3: Loose Project Data
Data belongs to a project but not to any organized campaign.

```python
project = Project.objects.get(project_type='radiation')
# No mission or campaign
track = Track.objects.create(project=project, campaign=None, ...)
# OR
measurement = RadiationMeasurement.objects.create(
    project=project,
    campaign=None,
    track=None,
    ...
)
```

**Hierarchy:**
```
Project → Track → Measurements  (no mission/campaign)
Project → Measurements          (no mission/campaign/track)
```

---

### Case 4: Mixed Data
Some data is organized in campaigns, some is loose.

```python
project = Project.objects.get(project_type='radiation')

# Organized data
mission = Mission.objects.create(name="Official Campaign", project=project, ...)
campaign = Campaign.objects.create(name="Phase 1", mission=mission, ...)
track1 = Track.objects.create(project=project, campaign=campaign, ...)

# Loose data (testing, calibration, etc.)
track2 = Track.objects.create(project=project, campaign=None, ...)
measurement = RadiationMeasurement.objects.create(
    project=project,
    campaign=None,
    ...
)
```

**Hierarchy:**
```
Project
  ├─ Mission → Campaign → Track1 → Measurements (organized)
  ├─ Track2 → Measurements (loose)
  └─ Measurements (loose, no track)
```

---

## Querying Data

### Get all data for a project
```python
project = Project.objects.get(id=1)

# All measurements (organized and loose)
all_radiation = RadiationMeasurement.objects.filter(project=project)

# Only organized data (with campaign)
organized = RadiationMeasurement.objects.filter(
    project=project,
    campaign__isnull=False
)

# Only loose data (without campaign)
loose = RadiationMeasurement.objects.filter(
    project=project,
    campaign__isnull=True
)
```

### Get all data for a campaign
```python
campaign = Campaign.objects.get(id=1)

# All measurements in campaign
measurements = RadiationMeasurement.objects.filter(campaign=campaign)

# All tracks in campaign
tracks = Track.objects.filter(campaign=campaign)

# Access parent project
project = campaign.project  # Through mission
```

### Get all data for a mission
```python
mission = Mission.objects.get(id=1)

# All campaigns in mission
campaigns = mission.campaigns.all()

# All measurements in any campaign of this mission
from django.db.models import Q
measurements = RadiationMeasurement.objects.filter(
    campaign__mission=mission
)
```

---

## Database Indexes

For optimal query performance, the following indexes are created:

**BaseMeasurement indexes:**
- `dateTime` - Temporal queries
- `latitude, longitude` - Spatial queries
- `project, dateTime` - Project temporal queries
- `device, dateTime` - Device temporal queries
- `campaign` - Campaign queries (NEW)
- `track` - Track queries

---

## Migration Notes

When migrating existing data:

1. All existing measurements must have a `project`
2. Existing measurements with `campaign` foreign key will keep it
3. Measurements without `campaign` will have `campaign=None` (allowed)
4. All missions must have a `project` (default project ID=1 was set during migration)

---

**Last Updated:** 2025-11-03
