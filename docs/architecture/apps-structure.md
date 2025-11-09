# Django Apps Structure

Detailed breakdown of OpenRed's Django applications and their responsibilities.

## Overview

OpenRed follows Django's **"app per concern"** philosophy, with each app encapsulating a specific domain:

```
openred/                      # Project root
├── devices/                  # Device registration & management
├── measures/                 # Measurement data & track processing
├── missions/                 # Organizational hierarchy (Project/Mission/Campaign)
├── users/                    # User accounts & authentication
├── frontend/                 # Web UI (optional)
└── openred/                  # Django project settings
```

## App Details

### `missions/` - Organizational Structure

**Purpose:** Manage the project hierarchy (Project → Mission → Campaign)

#### Models (`missions/models.py`)

- **`Project`** - Top-level measurement project
  - Fields: `name`, `project_type`, `is_public`, `is_active`, `created_by`
  - Types: `radiation` or `light_pollution`
  - Controls schema and permissions

- **`Mission`** - Abstract organizational grouping
  - Fields: `project`, `name`, `description`, `start_date`, `end_date`
  - Optional level between Project and Campaign

- **`Campaign`** - Specific data collection event
  - Fields: `mission`, `name`, `participants` (M2M), `devices` (M2M)
  - Associates users and equipment

#### API (`missions/views.py`)

- **`ProjectViewSet`** - Read-only project listing
  - GET `/api/projects/` - List all visible projects
  - GET `/api/projects/{id}/` - Project details
  - Permissions: Public projects visible to all, private to owners

- **`MissionViewSet`** - Read-only mission listing
  - GET `/api/missions/` - List missions
  - Filter by project: `?project=1`

- **`CampaignViewSet`** - Read-only campaign listing
  - GET `/api/campaigns/` - List campaigns
  - Filter by mission: `?mission=1`

#### Serializers (`missions/serializers.py`)

- **`ProjectSerializer`** - Includes computed fields
  - `mission_count`, `measurement_count`, `device_count`
  - Prevents N+1 queries with annotations

- **`MissionSerializer`** - Basic mission data
- **`CampaignSerializer`** - Campaign with participants/devices

#### Admin (`missions/admin.py`)

- **`ProjectAdmin`** - Create/edit projects
  - Computed fields: measurement count, mission count
  - Filter by type, status, public/private

- **`MissionAdmin`** - Mission management
- **`CampaignAdmin`** - Campaign with inline participants

#### Key Features

✅ Read-only API (organizational structure via admin only)  
✅ Hierarchical permissions (project owner controls access)  
✅ Flexible schema (missions and campaigns are optional)  
✅ Computed statistics (counts, aggregations)

---

### `measures/` - Measurement Data

**Purpose:** Store and process measurement data from sensors

#### Models (`measures/models.py`)

- **`RadiationMeasurement`** - Gamma radiation data
  - Fields: `project`, `device`, `campaign`, `track`, `latitude`, `longitude`, `cpm`, `usv_h`, `dateTime`
  - Geographic: PostGIS `PointField`
  - H3 Index: `h3_index` for hexagonal aggregation

- **`LightPollutionMeasurement`** - Light pollution data
  - Fields: `project`, `device`, `mpsas`, `nelm`, `dateTime`
  - Similar structure to RadiationMeasurement

- **`Track`** - File upload for batch import
  - Fields: `file`, `project`, `device`, `campaign`, `status`
  - Status: `pending` → `processing` → `completed`/`failed`
  - File formats: CSV, GPX

#### API (`measures/views.py`)

- **`RadiationMeasurementViewSet`** - Full CRUD
  - GET `/api/radiation-measurements/` - List measurements
  - POST `/api/radiation-measurements/` - Create measurement
  - GET `/api/radiation-measurements/{id}/` - Retrieve one
  - PUT/PATCH `/api/radiation-measurements/{id}/` - Update
  - DELETE `/api/radiation-measurements/{id}/` - Delete
  - Filters: `?project=1`, `?device=5`, `?dateTime__gte=2024-01-01`

- **`LightPollutionMeasurementViewSet`** - Full CRUD (similar)

- **`TrackViewSet`** - Track upload
  - POST `/api/tracks/upload/` - Upload CSV/GPX
  - GET `/api/tracks/{id}/status/` - Check processing status

#### Serializers (`measures/serializers.py`)

- **`RadiationMeasurementSerializer`**
  - Validates geographic coordinates
  - Auto-calculates H3 index
  - Handles foreign key lookups

- **`TrackSerializer`**
  - File upload validation
  - Status field (read-only)

#### Tasks (`measures/tasks.py`)

- **`process_track(track_id)`** - Background job
  - Parse CSV/GPX file
  - Validate rows
  - Bulk create measurements
  - Update track status
  - Error handling and logging

#### Management Commands

- **`add_measures.py`** - Bulk import from CLI
  ```bash
  python manage.py add_measures --file data.csv --project 1 --device 5
  ```

#### Key Features

✅ Full CRUD API for measurements  
✅ Background CSV/GPX processing (RQ)  
✅ Geographic queries with PostGIS  
✅ H3 hexagonal indexing for aggregation  
✅ Complex filtering and pagination  
✅ Bulk operations support

---

### `devices/` - Device Management

**Purpose:** Register and manage measurement devices

#### Models (`devices/models.py`)

- **`Device`**
  - Fields: `device_id` (unique), `name`, `device_type`, `owner`, `is_active`
  - Types: Device type codes (e.g., "GAMMA_SCOUT", "SQM")
  - Ownership: One user per device

#### API (`devices/views.py`)

- **`DeviceViewSet`** - Full CRUD
  - GET `/api/devices/` - List user's devices
  - POST `/api/devices/` - Register new device
  - PUT/PATCH `/api/devices/{id}/` - Update device
  - DELETE `/api/devices/{id}/` - Remove device

#### Serializers (`devices/serializers.py`)

- **`DeviceSerializer`**
  - Validates `device_id` uniqueness
  - Auto-assigns owner to current user

#### Forms (`devices/forms.py`)

- **`DeviceForm`** - Web UI form (if using frontend)

#### Key Features

✅ Unique device identifiers  
✅ Owner-based access control  
✅ Device type validation  
✅ Active/inactive status

---

### `users/` - User Management

**Purpose:** Custom user model and authentication

#### Models (`users/models.py`)

- **`CustomUser`** (extends Django `AbstractUser`)
  - Additional fields for user profile
  - Email verification requirements

#### Adapters (`users/adapters.py`)

- **`CustomAccountAdapter`** - django-allauth customization
  - Custom registration behavior
  - Email verification logic

#### API (`users/views.py`)

- **`UserRegistrationView`** - Register new user
  - POST `/api/users/register/`
  - Email verification workflow

- **`UserProfileView`** - User profile
  - GET `/api/users/me/` - Current user profile

#### Serializers (`users/serializers.py`)

- **`UserSerializer`** - User data representation
- **`RegisterSerializer`** - Registration validation

#### Key Features

✅ Email-based authentication  
✅ Email verification workflow  
✅ Token-based API auth  
✅ User profile management

---

### `frontend/` - Web UI (Optional)

**Purpose:** Template-based web interface

#### Views (`frontend/views.py`)

- **`index`** - Home page
- **`add_device`** - Device registration form
- **Dashboard views** - Data visualization

#### Templates (`frontend/templates/`)

- **`base.html`** - Base template with Bootstrap
- **`index.html`** - Landing page
- **`add_device.html`** - Device form

#### Static Files (`frontend/static/`)

- **`css/styles.css`** - Custom styles
- **`js/`** - JavaScript for interactivity

#### Key Features

✅ Bootstrap-based UI  
✅ Django template system  
✅ Form handling  
⚠️ Optional - API-first architecture

---

### `openred/` - Django Project Settings

**Purpose:** Global configuration and URL routing

#### Files

- **`settings.py`** - Django settings
  - Database configuration (PostgreSQL)
  - Installed apps
  - Middleware
  - DRF settings
  - Redis/RQ configuration

- **`urls.py`** - Root URL configuration
  ```python
  urlpatterns = [
      path('admin/', admin.site.urls),
      path('api/', include('devices.urls')),
      path('api/', include('measures.urls')),
      path('api/', include('missions.urls')),
      path('accounts/', include('allauth.urls')),
      path('', include('frontend.urls')),
  ]
  ```

- **`wsgi.py`** - WSGI application
- **`asgi.py`** - ASGI application (for async)

---

## App Dependencies

```
┌─────────┐
│ users   │ (base - no dependencies)
└────┬────┘
     │
     ▼
┌─────────┐         ┌─────────┐
│ devices │         │ missions│
└────┬────┘         └────┬────┘
     │                   │
     └────────┬──────────┘
              │
              ▼
         ┌─────────┐
         │ measures│ (depends on all)
         └─────────┘
              │
              ▼
         ┌─────────┐
         │ frontend│ (UI layer)
         └─────────┘
```

**Dependency Rules:**
- `measures` depends on `devices`, `missions`, `users`
- `missions` depends on `users`
- `devices` depends on `users`
- `frontend` depends on all (presentation layer)

## File Structure per App

Standard Django app structure:

```
app_name/
├── __init__.py           # Python package marker
├── admin.py              # Django admin configuration
├── apps.py               # App configuration
├── models.py             # Database models
├── serializers.py        # DRF serializers (API apps)
├── views.py              # API views (ViewSets) or template views
├── urls.py               # URL routing
├── forms.py              # Django forms (optional)
├── tests.py              # Unit tests
├── tasks.py              # RQ background tasks (optional)
├── migrations/           # Database migrations
│   ├── __init__.py
│   ├── 0001_initial.py
│   └── ...
├── management/           # Custom management commands (optional)
│   ├── __init__.py
│   └── commands/
│       ├── __init__.py
│       └── custom_command.py
└── static/               # Static files (CSS/JS) - frontend only
└── templates/            # HTML templates - frontend only
```

## Database Relationships

Cross-app model relationships:

```python
# measures/models.py
class RadiationMeasurement(models.Model):
    project = models.ForeignKey('missions.Project')  # Cross-app FK
    device = models.ForeignKey('devices.Device')     # Cross-app FK
    created_by = models.ForeignKey('users.CustomUser')  # Cross-app FK
    campaign = models.ForeignKey('missions.Campaign', null=True)
    track = models.ForeignKey('measures.Track', null=True)
```

## API URL Structure

RESTful API organization:

```
/api/
├── projects/                    (missions app)
│   ├── GET     List projects
│   └── {id}/
│       └── GET   Project detail
├── missions/                    (missions app)
│   └── GET     List missions
├── campaigns/                   (missions app)
│   └── GET     List campaigns
├── devices/                     (devices app)
│   ├── GET     List devices
│   ├── POST    Create device
│   └── {id}/
│       ├── GET     Device detail
│       ├── PUT     Update device
│       └── DELETE  Delete device
├── radiation-measurements/      (measures app)
│   ├── GET     List measurements
│   ├── POST    Create measurement
│   └── {id}/
│       ├── GET     Measurement detail
│       ├── PUT     Update measurement
│       └── DELETE  Delete measurement
├── light-pollution-measurements/ (measures app)
│   └── (same as radiation)
├── tracks/                      (measures app)
│   ├── POST    Upload track
│   └── {id}/
│       └── status/
│           └── GET   Track processing status
└── users/                       (users app)
    ├── register/  POST
    └── me/        GET
```

## Admin Interface Organization

```
Django Admin
├── AUTHENTICATION AND AUTHORIZATION
│   ├── Users
│   └── Groups
├── DEVICES
│   └── Devices
├── MEASURES
│   ├── Radiation Measurements
│   ├── Light Pollution Measurements
│   └── Tracks
└── MISSIONS
    ├── Projects
    ├── Missions
    └── Campaigns
```

## Testing Structure

Each app has `tests.py` or `tests/` directory:

```python
# measures/tests.py
from django.test import TestCase
from rest_framework.test import APITestCase

class RadiationMeasurementTests(APITestCase):
    def test_create_measurement(self):
        # Test measurement creation
        pass
    
    def test_track_upload(self):
        # Test CSV upload
        pass
```

Run tests:
```bash
python manage.py test measures
python manage.py test missions
python manage.py test devices
```

## Further Reading

- **[Data Hierarchy](data-hierarchy.md)** - Understand the organizational structure
- **[Background Jobs](background-jobs.md)** - RQ task processing
- **[API Reference](../api-reference/missions.md)** - Complete API documentation
- **[Development Guide](../development/contributing.md)** - Contributing to OpenRed
