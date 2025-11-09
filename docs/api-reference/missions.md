# Missions API Reference

API endpoints for organizational hierarchy (Projects, Missions, Campaigns).

## Overview

The Missions API provides **read-only** access to the organizational structure. Creating and modifying these entities must be done through the Django Admin interface.

**Base URL:** `/api/`

**Authentication:** Token-based (`Authorization: Token <your-token>`)

---

## ViewSets

### ProjectViewSet

Manages project listing and retrieval.

::: missions.views.ProjectViewSet
    options:
      show_root_heading: true
      show_source: false
      members:
        - list
        - retrieve
      heading_level: 4

---

### MissionViewSet

Manages mission listing and retrieval.

::: missions.views.MissionViewSet
    options:
      show_root_heading: true
      show_source: false
      members:
        - list
        - retrieve
      heading_level: 4

---

### CampaignViewSet

Manages campaign listing and retrieval.

::: missions.views.CampaignViewSet
    options:
      show_root_heading: true
      show_source: false
      members:
        - list
        - retrieve
      heading_level: 4

---

## Models

### Project

::: missions.models.Project
    options:
      show_root_heading: true
      show_source: false
      members:
        - __str__
        - save
      heading_level: 4

---

### Mission

::: missions.models.Mission
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

### Campaign

::: missions.models.Campaign
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

## Serializers

### ProjectSerializer

::: missions.serializers.ProjectSerializer
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

### MissionSerializer

::: missions.serializers.MissionSerializer
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

### CampaignSerializer

::: missions.serializers.CampaignSerializer
    options:
      show_root_heading: true
      show_source: false
      heading_level: 4

---

## API Examples

### List All Projects

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
     https://api.openred.org/api/projects/
```

**Response:**
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1,
      "name": "European Radiation Network",
      "project_type": "radiation",
      "description": "Pan-European gamma radiation monitoring",
      "is_public": true,
      "is_active": true,
      "created_by": 5,
      "created_at": "2024-01-15T10:30:00Z",
      "mission_count": 3,
      "measurement_count": 15420,
      "device_count": 12
    },
    {
      "id": 2,
      "name": "City Light Pollution Study",
      "project_type": "light_pollution",
      "is_public": false,
      "is_active": true,
      "created_by": 5,
      "mission_count": 1,
      "measurement_count": 8934,
      "device_count": 6
    }
  ]
}
```

---

### Get Project Details

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
     https://api.openred.org/api/projects/1/
```

**Response:**
```json
{
  "id": 1,
  "name": "European Radiation Network",
  "project_type": "radiation",
  "description": "Pan-European gamma radiation monitoring network...",
  "is_public": true,
  "is_active": true,
  "created_by": 5,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-11-20T14:22:00Z",
  "project_settings": {
    "auto_approve_devices": true,
    "data_retention_days": 730
  },
  "mission_count": 3,
  "measurement_count": 15420,
  "device_count": 12
}
```

---

### List Missions (Filtered by Project)

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
     "https://api.openred.org/api/missions/?project=1"
```

**Response:**
```json
{
  "count": 3,
  "results": [
    {
      "id": 1,
      "project": 1,
      "name": "Q4 2024 Campaign",
      "description": "Autumn monitoring campaign",
      "start_date": "2024-10-01",
      "end_date": "2024-12-31",
      "created_by": 5,
      "created_at": "2024-09-28T09:00:00Z"
    },
    {
      "id": 2,
      "name": "Urban Area Monitoring",
      "project": 1,
      "start_date": "2024-06-01",
      "end_date": null
    }
  ]
}
```

---

### List Campaigns (Filtered by Mission)

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
     "https://api.openred.org/api/campaigns/?mission=1"
```

**Response:**
```json
{
  "count": 2,
  "results": [
    {
      "id": 1,
      "mission": 1,
      "name": "Northern Region - Week 1",
      "description": "Field work in northern areas",
      "start_date": "2024-10-01",
      "end_date": "2024-10-07",
      "participants": [12, 15, 18],
      "devices": [3, 5, 7],
      "created_at": "2024-09-25T10:00:00Z"
    }
  ]
}
```

---

## Permissions

### Project Visibility

- **Public projects** (`is_public=true`): Visible to all authenticated users
- **Private projects** (`is_public=false`): Only visible to:
  - Project owner (`created_by`)
  - Users with explicit permissions (future feature)

### Write Operations

All write operations (POST, PUT, PATCH, DELETE) are **disabled** via API.

Use **Django Admin** to:
- Create projects
- Create missions and campaigns
- Modify organizational structure
- Set permissions

---

## Query Parameters

### Filtering

**Projects:**
- `?project_type=radiation` - Filter by type
- `?is_public=true` - Only public projects
- `?is_active=true` - Only active projects

**Missions:**
- `?project=1` - Missions in specific project

**Campaigns:**
- `?mission=1` - Campaigns in specific mission

### Pagination

```bash
# Default: 100 items per page
GET /api/projects/

# Custom page size
GET /api/projects/?page_size=50

# Navigate pages
GET /api/projects/?page=2
```

### Ordering

```bash
# Order by creation date (newest first)
GET /api/projects/?ordering=-created_at

# Order by name (alphabetical)
GET /api/projects/?ordering=name
```

---

## Status Codes

| Code | Meaning | Usage |
|------|---------|-------|
| 200 | OK | Successful GET request |
| 401 | Unauthorized | Missing or invalid token |
| 403 | Forbidden | No permission to view project |
| 404 | Not Found | Project/Mission/Campaign doesn't exist |
| 405 | Method Not Allowed | Attempted POST/PUT/DELETE (read-only) |

---

## Related Documentation

- **[Data Hierarchy](../architecture/data-hierarchy.md)** - Understanding the organizational structure
- **[Measures API](measures.md)** - Measurement endpoints
- **[Authentication](../guides/authentication.md)** - Getting your API token
- **[Quick Start Guide](../getting-started/quick-start.md)** - Complete tutorial
