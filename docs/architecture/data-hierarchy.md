# Data Hierarchy

Understanding OpenRed's data organization structure.

## Overview

OpenRed uses a flexible hierarchy that allows data to be organized at different levels depending on organizational needs. This structure balances simplicity with powerful organizational capabilities.

```
Project (required) ← Type of measurement (radiation/light_pollution)
  ├─ Mission (optional) ← Abstract organizational grouping
  │    └─ Campaign (optional) ← Specific data collection event
  │         ├─ Track ← CSV/GPX file import
  │         └─ Measurement ← Individual data point
  ├─ Track (without campaign)
  └─ Measurement (without campaign)
```

## Hierarchy Levels

### 1. Project (Required)

**Purpose:** Defines the type of measurement system and data schema

- **Required:** YES - All data must belong to a project
- **Type:** `radiation` or `light_pollution`
- **Controls:** Data schema, visibility (public/private), and permissions
- **Model:** `missions.Project`

**Example Use Cases:**
- "European Gamma Radiation Network"
- "City Light Pollution Study 2024"
- "Academic Research - Cosmic Rays"

**Key Fields:**
- `project_type`: Determines measurement schema (radiation vs light pollution)
- `is_public`: Controls API visibility
- `is_active`: Enable/disable data collection
- `project_settings`: JSON configuration for custom behavior

### 2. Mission (Optional)

**Purpose:** Abstract organizational grouping within a project

- **Required:** NO - Direct Project → Measurement is valid
- **Use When:** You need thematic or temporal organization
- **Model:** `missions.Mission`

**Example Use Cases:**
- "Autumn 2024 Campaign"
- "Urban Area Monitoring"
- "Post-Event Measurements"

**Key Fields:**
- `project`: Parent project (required)
- `start_date` / `end_date`: Time boundaries
- `created_by`: Mission creator

### 3. Campaign (Optional)

**Purpose:** Specific data collection event with participants and devices

- **Required:** NO - Direct Mission → Measurement is valid
- **Use When:** You have coordinated field work with specific team/equipment
- **Model:** `missions.Campaign`

**Example Use Cases:**
- "Barcelona Field Study - Week 1"
- "School Collaboration - Spring 2024"
- "Device Calibration Session"

**Key Fields:**
- `mission`: Parent mission (required)
- `participants`: Users involved (ManyToMany)
- `devices`: Equipment used (ManyToMany)
- `start_date` / `end_date`: Campaign duration

### 4. Track (Optional)

**Purpose:** Batch import from CSV/GPX files

- **Required:** NO - Can create measurements directly via API
- **Use When:** Uploading data from logged files
- **Model:** `measures.Track`
- **Processing:** Asynchronous via RQ worker

**Key Features:**
- Supports CSV and GPX formats
- Background processing (non-blocking)
- Status tracking: `pending` → `processing` → `completed`/`failed`
- Automatic measurement extraction

### 5. Measurement (Always Present)

**Purpose:** Individual data point

- **Required:** YES - The actual measurement data
- **Models:** 
  - `measures.RadiationMeasurement` (for radiation projects)
  - `measures.LightPollutionMeasurement` (for light pollution projects)

**Required Fields:**
- `project`: Parent project (required)
- `device`: Measurement device (required)
- `dateTime`: Timestamp
- `latitude` / `longitude`: Location
- Type-specific fields (e.g., `cpm`, `mpsas`)

## Relationship Rules

### Project → Mission

```python
# Missions are optional
project = Project.objects.get(id=1)

# Option 1: Create missions for organization
mission = Mission.objects.create(
    project=project,
    name="Autumn Campaign",
    start_date="2024-09-01",
    end_date="2024-11-30"
)

# Option 2: Skip missions entirely
measurement = RadiationMeasurement.objects.create(
    project=project,  # Direct to project
    campaign=None,    # No campaign
    device=device,
    ...
)
```

### Mission → Campaign

```python
# Campaigns are optional
mission = Mission.objects.get(id=1)

# Option 1: Create campaigns for events
campaign = Campaign.objects.create(
    mission=mission,
    name="Field Study Week 1",
    start_date="2024-09-01",
    end_date="2024-09-07"
)

# Option 2: Skip campaigns
measurement = RadiationMeasurement.objects.create(
    project=mission.project,
    campaign=None,  # No campaign
    device=device,
    ...
)
```

### Track → Measurement

```python
# Upload a CSV file (creates track)
track = Track.objects.create(
    file='measurements.csv',
    project=project,
    device=device,
    campaign=campaign  # Optional
)

# Background job creates measurements linked to track
# measurement.track = track
```

## Access Patterns

### Via Campaign (Full Hierarchy)

```python
campaign = Campaign.objects.get(id=1)

# Access parent mission
mission = campaign.mission

# Access grandparent project
project = campaign.mission.project  # or campaign.project (property)

# Get all measurements in this campaign
measurements = RadiationMeasurement.objects.filter(campaign=campaign)

# Get all tracks in this campaign
tracks = Track.objects.filter(campaign=campaign)
```

### Via Track

```python
track = Track.objects.get(id=1)

# Get all measurements from this track
measurements = track.radiationmeasurement_set.all()
# or
measurements = track.lightpollutionmeasurement_set.all()

# Access campaign (if exists)
if track.campaign:
    campaign = track.campaign
    mission = campaign.mission
```

### Via Measurement

```python
measurement = RadiationMeasurement.objects.get(id=1)

# Direct project access
project = measurement.project

# Campaign access (if exists)
if measurement.campaign:
    campaign = measurement.campaign
    mission = campaign.mission

# Track access (if imported from file)
if measurement.track:
    track = measurement.track
```

## Use Case Examples

### Simple Use Case: Direct Upload

No missions or campaigns needed:

```python
# Just Project + Measurements
project = Project.objects.create(name="Simple Monitoring", project_type="radiation")

measurement = RadiationMeasurement.objects.create(
    project=project,
    device=device,
    latitude=40.4168,
    longitude=-3.7038,
    cpm=100,
    dateTime=timezone.now()
)
```

### Medium Complexity: CSV Upload

Using tracks for batch import:

```python
project = Project.objects.create(name="Field Study", project_type="radiation")

# Upload CSV file
track = Track.objects.create(
    file='field_data.csv',
    project=project,
    device=device
)

# RQ worker processes file and creates measurements
# with track=track linkage
```

### Full Hierarchy: Organized Campaign

Complete organizational structure:

```python
# Create project
project = Project.objects.create(
    name="Regional Monitoring 2024",
    project_type="radiation"
)

# Create mission
mission = Mission.objects.create(
    project=project,
    name="Q4 2024 Campaign",
    start_date="2024-10-01",
    end_date="2024-12-31"
)

# Create campaign
campaign = Campaign.objects.create(
    mission=mission,
    name="Northern Region - Week 1",
    start_date="2024-10-01",
    end_date="2024-10-07"
)

# Add participants and devices
campaign.participants.add(user1, user2)
campaign.devices.add(device1, device2)

# Upload track
track = Track.objects.create(
    file='week1_data.csv',
    project=project,
    campaign=campaign,
    device=device1
)

# Or create measurements directly
measurement = RadiationMeasurement.objects.create(
    project=project,
    campaign=campaign,
    device=device1,
    ...
)
```

## Best Practices

### When to Use Missions

✅ **Use missions when:**
- You have multiple phases or time periods
- You want thematic organization
- You need to group multiple campaigns

❌ **Skip missions when:**
- Simple, continuous monitoring
- Only one campaign or no campaigns
- Organizational overhead isn't needed

### When to Use Campaigns

✅ **Use campaigns when:**
- Coordinating field work with multiple people
- Specific equipment deployed for an event
- Need to track participation and resources

❌ **Skip campaigns when:**
- Individual data collection
- Automated sensor networks
- Simplicity is preferred

### When to Use Tracks

✅ **Use tracks when:**
- Importing CSV/GPX files
- Need to maintain file lineage
- Batch processing large datasets

❌ **Skip tracks when:**
- Real-time API uploads
- Individual measurements
- No file import needed

## Database Schema

The actual database relationships:

```
missions_project (1) ─────┬─── (N) missions_mission
                          │
                          ├─── (N) measures_track
                          │
                          └─── (N) measures_radiationmeasurement
                                   measures_lightpollutionmeasurement

missions_mission (1) ───── (N) missions_campaign

missions_campaign (1) ───┬─── (N) measures_track
                         │
                         └─── (N) measures_radiationmeasurement
                                  measures_lightpollutionmeasurement

measures_track (1) ─────── (N) measures_radiationmeasurement
                                measures_lightpollutionmeasurement
```

## API Implications

### Read-Only Organizational Endpoints

Projects, Missions, and Campaigns are **read-only via API**:

```bash
# GET only - no POST/PUT/DELETE
GET /api/projects/
GET /api/missions/
GET /api/campaigns/
```

Use Django Admin to create/modify organizational structures.

### Full CRUD for Measurements

```bash
# Full CRUD operations
GET/POST /api/radiation-measurements/
GET/PUT/PATCH/DELETE /api/radiation-measurements/{id}/
```

### Track Uploads

```bash
# Special upload endpoint
POST /api/tracks/upload/
GET /api/tracks/{id}/status/
```

## Further Reading

- **[DateTime Fields](datetime-fields.md)** - Understanding time fields
- **[Uploading Tracks](../guides/uploading-tracks.md)** - Complete track upload guide
- **[API Reference](../api-reference/missions.md)** - Detailed API documentation

---

For the complete, detailed technical specification, see [HIERARCHY.md](../../../HIERARCHY.md) in the project root.
