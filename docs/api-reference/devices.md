# Devices API Reference

Device models and device instance management endpoints.

## Overview

The Devices API manages two types of resources:

1. **Device Models** - Specifications and technical details of measurement devices
2. **Devices** - Individual device instances with serial numbers and ownership

Every measurement in OpenRed must be associated with a registered Device.

## Device Models

### List Device Models

#### `GET /api/device-models/`

List all available device models with specifications.

**Response (200 OK):**
```json
[
  {
    "id": 1,
    "name": "RadiationD-Cajal",
    "manufacturer": "RadiationD",
    "version": "v2.1",
    "technology": "Geiger-Müller tube SBM-20",
    "validatedByOpenRed": true,
    "description": "ESP32-based radiation monitor with GPS and WiFi",
    "picture": "https://api.open-red.es/media/device_pictures/radiationd_cajal.jpg",
    "max_radiation_range": 999.0
  },
  {
    "id": 2,
    "name": "GammaScout Standard",
    "manufacturer": "International Medcom",
    "version": null,
    "technology": "Energy-compensated Geiger-Müller tube",
    "validatedByOpenRed": true,
    "description": "Professional handheld radiation detector",
    "picture": null,
    "max_radiation_range": 1000.0
  },
  {
    "id": 3,
    "name": "SQM-LU-DL",
    "manufacturer": "Unihedron",
    "version": "DL",
    "technology": "TAOS TSL237S light-to-frequency sensor",
    "validatedByOpenRed": true,
    "description": "Sky Quality Meter with data logger",
    "picture": null,
    "max_radiation_range": null
  }
]
```

**Query Parameters:**
- `validatedByOpenRed=true` - Filter to validated devices only
- `manufacturer=RadiationD` - Filter by manufacturer

**Example:**
```bash
curl https://api.open-red.es/api/device-models/?validatedByOpenRed=true
```

---

### Get Device Model Details

#### `GET /api/device-models/{id}/`

Retrieve detailed specifications for a specific device model.

**Response (200 OK):**
```json
{
  "id": 1,
  "name": "RadiationD-Cajal",
  "manufacturer": "RadiationD",
  "version": "v2.1",
  "technology": "Geiger-Müller tube SBM-20",
  "validatedByOpenRed": true,
  "description": "ESP32-based radiation monitor with GPS and WiFi. Features automatic data logging, real-time clock, and battery operation. Calibrated for environmental radiation monitoring.",
  "picture": "https://api.open-red.es/media/device_pictures/radiationd_cajal.jpg",
  "max_radiation_range": 999.0,
  "typical_accuracy": "±20%",
  "measurement_units": "μSv/h, CPM"
}
```

---

### Create Device Model (Admin Only)

#### `POST /api/device-models/`

Create a new device model specification.

**Headers:**
```
Authorization: Token <admin-token>
Content-Type: application/json
```

**Request Body:**
```json
{
  "name": "Radex RD1212",
  "manufacturer": "Quarta-Rad",
  "version": "BT",
  "technology": "Geiger-Müller tube",
  "validatedByOpenRed": false,
  "description": "Compact radiation detector with Bluetooth connectivity",
  "max_radiation_range": 999.0
}
```

**Response (201 Created):**
```json
{
  "id": 4,
  "name": "Radex RD1212",
  "manufacturer": "Quarta-Rad",
  "version": "BT",
  "technology": "Geiger-Müller tube",
  "validatedByOpenRed": false,
  "description": "Compact radiation detector with Bluetooth connectivity",
  "picture": null,
  "max_radiation_range": 999.0
}
```

---

### Update Device Model (Admin Only)

#### `PUT /api/device-models/{id}/`
#### `PATCH /api/device-models/{id}/`

Update device model specifications. Use `PUT` for full update, `PATCH` for partial.

**Headers:**
```
Authorization: Token <admin-token>
Content-Type: application/json
```

**Request Body (PATCH example):**
```json
{
  "validatedByOpenRed": true,
  "description": "Validated for OpenRed network. Calibrated and tested."
}
```

**Response (200 OK):**
```json
{
  "id": 4,
  "name": "Radex RD1212",
  "manufacturer": "Quarta-Rad",
  "version": "BT",
  "technology": "Geiger-Müller tube",
  "validatedByOpenRed": true,
  "description": "Validated for OpenRed network. Calibrated and tested.",
  "picture": null,
  "max_radiation_range": 999.0
}
```

---

## Devices

### List Devices

#### `GET /api/devices/`

List registered device instances. Returns only devices owned by authenticated user (or all devices for admins).

**Headers:**
```
Authorization: Token <your-token>
```

**Response (200 OK):**
```json
[
  {
    "id": 101,
    "device_model": {
      "id": 1,
      "name": "RadiationD-Cajal",
      "manufacturer": "RadiationD"
    },
    "serial_number": "RDC-2024-001",
    "hash": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
    "owner": {
      "id": 42,
      "username": "scientist123",
      "email": "scientist@example.com"
    },
    "purchase_date": "2024-01-15",
    "calibration_date": "2024-01-20",
    "is_active": true
  },
  {
    "id": 102,
    "device_model": {
      "id": 3,
      "name": "SQM-LU-DL",
      "manufacturer": "Unihedron"
    },
    "serial_number": "SQM-12345",
    "hash": "z9y8x7w6v5u4t3s2r1q0p9o8n7m6l5k4",
    "owner": {
      "id": 42,
      "username": "scientist123",
      "email": "scientist@example.com"
    },
    "purchase_date": "2023-06-10",
    "calibration_date": "2024-06-10",
    "is_active": true
  }
]
```

**Query Parameters:**
- `device_model=1` - Filter by device model ID
- `is_active=true` - Show only active devices
- `serial_number=RDC-2024-001` - Search by serial number

---

### Get Device Details

#### `GET /api/devices/{id}/`

Retrieve detailed information about a specific device.

**Headers:**
```
Authorization: Token <your-token>
```

**Response (200 OK):**
```json
{
  "id": 101,
  "device_model": {
    "id": 1,
    "name": "RadiationD-Cajal",
    "manufacturer": "RadiationD",
    "version": "v2.1",
    "technology": "Geiger-Müller tube SBM-20",
    "validatedByOpenRed": true,
    "max_radiation_range": 999.0
  },
  "serial_number": "RDC-2024-001",
  "hash": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
  "owner": {
    "id": 42,
    "username": "scientist123",
    "email": "scientist@example.com",
    "first_name": "Jane",
    "last_name": "Doe"
  },
  "purchase_date": "2024-01-15",
  "calibration_date": "2024-01-20",
  "is_active": true,
  "total_measurements": 1523,
  "last_measurement_date": "2024-11-10T14:30:00Z"
}
```

---

### Register Device

#### `POST /api/devices/`

Register a new device instance.

**Headers:**
```
Authorization: Token <your-token>
Content-Type: application/json
```

**Request Body:**
```json
{
  "device_model": 1,
  "serial_number": "RDC-2024-042",
  "purchase_date": "2024-11-01",
  "calibration_date": "2024-11-05",
  "is_active": true
}
```

**Response (201 Created):**
```json
{
  "id": 103,
  "device_model": {
    "id": 1,
    "name": "RadiationD-Cajal",
    "manufacturer": "RadiationD"
  },
  "serial_number": "RDC-2024-042",
  "hash": "f1e2d3c4b5a69788676564534231201",
  "owner": {
    "id": 42,
    "username": "scientist123",
    "email": "scientist@example.com"
  },
  "purchase_date": "2024-11-01",
  "calibration_date": "2024-11-05",
  "is_active": true
}
```

**Validation:**
- `serial_number` is required and must be unique
- `device_model` must be a valid device model ID
- `hash` is auto-generated from serial number (MD5)
- `owner` is automatically set to authenticated user

---

### Update Device

#### `PUT /api/devices/{id}/`
#### `PATCH /api/devices/{id}/`

Update device information. Users can only update their own devices.

**Headers:**
```
Authorization: Token <your-token>
Content-Type: application/json
```

**Request Body (PATCH example):**
```json
{
  "calibration_date": "2024-11-10",
  "is_active": true
}
```

**Response (200 OK):**
```json
{
  "id": 101,
  "device_model": {
    "id": 1,
    "name": "RadiationD-Cajal",
    "manufacturer": "RadiationD"
  },
  "serial_number": "RDC-2024-001",
  "hash": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
  "owner": {
    "id": 42,
    "username": "scientist123",
    "email": "scientist@example.com"
  },
  "purchase_date": "2024-01-15",
  "calibration_date": "2024-11-10",
  "is_active": true
}
```

---

### Deactivate Device

#### `PATCH /api/devices/{id}/`

Mark device as inactive (soft delete).

**Headers:**
```
Authorization: Token <your-token>
Content-Type: application/json
```

**Request Body:**
```json
{
  "is_active": false
}
```

**Response (200 OK):**
```json
{
  "id": 101,
  "is_active": false,
  ...
}
```

**Note:** Inactive devices are hidden from default listings but measurements remain accessible.

---

## Device Hash System

Each device has an auto-generated `hash` field (MD5 of serial number) used for:

1. **Quick device lookup** without exposing serial numbers
2. **API authentication** in embedded devices
3. **URL-safe identifiers** in device management

**Example:**
```python
import hashlib

serial = "RDC-2024-001"
device_hash = hashlib.md5(serial.encode()).hexdigest()
# Result: "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
```

---

## Frontend Integration Examples

### React Device Registration

```javascript
const registerDevice = async (deviceData) => {
  const token = localStorage.getItem('authToken');
  
  const response = await fetch('https://api.open-red.es/api/devices/', {
    method: 'POST',
    headers: {
      'Authorization': `Token ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(deviceData)
  });
  
  if (response.ok) {
    const device = await response.json();
    console.log('Device registered:', device);
    return device;
  }
};

// Usage
registerDevice({
  device_model: 1,
  serial_number: 'RDC-2024-042',
  purchase_date: '2024-11-01',
  calibration_date: '2024-11-05',
  is_active: true
});
```

### React Device Selector Component

```javascript
import { useState, useEffect } from 'react';

function DeviceSelector({ onSelect }) {
  const [devices, setDevices] = useState([]);
  const [deviceModels, setDeviceModels] = useState([]);
  
  useEffect(() => {
    const fetchDevices = async () => {
      const token = localStorage.getItem('authToken');
      
      // Fetch user's devices
      const devicesRes = await fetch('https://api.open-red.es/api/devices/', {
        headers: { 'Authorization': `Token ${token}` }
      });
      const devicesData = await devicesRes.json();
      setDevices(devicesData);
      
      // Fetch available device models
      const modelsRes = await fetch('https://api.open-red.es/api/device-models/');
      const modelsData = await modelsRes.json();
      setDeviceModels(modelsData);
    };
    
    fetchDevices();
  }, []);
  
  return (
    <select onChange={(e) => onSelect(e.target.value)}>
      <option value="">Select device...</option>
      {devices.map(device => (
        <option key={device.id} value={device.id}>
          {device.device_model.name} - {device.serial_number}
        </option>
      ))}
    </select>
  );
}
```

### Python SDK Device Management

```python
import requests

class OpenRedClient:
    def __init__(self, api_url, token):
        self.api_url = api_url
        self.headers = {'Authorization': f'Token {token}'}
    
    def register_device(self, device_model_id, serial_number, **kwargs):
        """Register a new device."""
        data = {
            'device_model': device_model_id,
            'serial_number': serial_number,
            **kwargs
        }
        response = requests.post(
            f'{self.api_url}/api/devices/',
            json=data,
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def get_my_devices(self, active_only=True):
        """Get user's devices."""
        params = {'is_active': 'true'} if active_only else {}
        response = requests.get(
            f'{self.api_url}/api/devices/',
            params=params,
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def update_calibration(self, device_id, calibration_date):
        """Update device calibration date."""
        data = {'calibration_date': calibration_date}
        response = requests.patch(
            f'{self.api_url}/api/devices/{device_id}/',
            json=data,
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

# Usage
client = OpenRedClient('https://api.open-red.es', 'your-token')

# Register device
device = client.register_device(
    device_model_id=1,
    serial_number='RDC-2024-042',
    purchase_date='2024-11-01',
    calibration_date='2024-11-05'
)

# Get active devices
devices = client.get_my_devices(active_only=True)

# Update calibration
client.update_calibration(device['id'], '2024-12-01')
```

---

## Permissions

### Device Models
- **List/Read:** Public (no authentication required)
- **Create/Update/Delete:** Admin only

### Devices
- **List:** Returns only devices owned by authenticated user (admins see all)
- **Read:** Device owner or admin
- **Create:** Authenticated users (auto-assigned as owner)
- **Update:** Device owner or admin
- **Delete:** Device owner or admin

---

## Validated Device Models

OpenRed validates certain device models for data quality assurance:

### Radiation Devices
- ✅ **RadiationD-Cajal** (RadiationD) - Validated
- ✅ **GammaScout Standard** (International Medcom) - Validated
- ⚠️ **Radex RD1212** (Quarta-Rad) - Not validated

### Light Pollution Devices
- ✅ **SQM-LU-DL** (Unihedron) - Validated
- ✅ **TESS-W** (STARS4ALL) - Validated

**Validation criteria:**
1. Calibrated against reference standards
2. Tested in field conditions
3. Data format verified
4. Reproducibility confirmed

---

## Related Documentation

- [Measurements API](measures.md) - Submit measurements from devices
- [Missions & Projects](missions.md) - Organize device data
- [Track Upload](../guides/uploading-tracks.md) - Bulk measurement import
