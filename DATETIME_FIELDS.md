# Campos de Fecha/Tiempo en OpenRed

## Resumen de Campos DateTime

Este documento aclara el propósito de cada campo de fecha/tiempo en los modelos de OpenRed.

---

## 📊 BaseMeasurement (Clase Abstracta)

Base para `RadiationMeasurement` y `LightPollutionMeasurement`.

### Campos de Tiempo de la Medición

| Campo | Tipo | Propósito | Origen |
|-------|------|-----------|--------|
| **`dateTime`** | DateTimeField | **⭐ Timestamp de CUANDO se tomó la medición** (dato del sensor) | CSV, dispositivo, manual |
| **`timestamp`** | BigIntegerField | Unix timestamp (copia de `dateTime` en formato numérico) | Auto-calculado desde `dateTime` |

**Uso:**
- `dateTime`: Fecha/hora real de la medición física (del sensor)
- `timestamp`: Versión numérica para cálculos y APIs que requieren Unix time

### Campos de Auditoría/Sistema

| Campo | Tipo | Propósito | Origen |
|-------|------|-----------|--------|
| **`created_at`** | DateTimeField (auto_now_add) | **Cuándo se CREÓ el registro** en la base de datos | Django (automático) |

**Uso:**
- `created_at`: Cuándo se guardó el registro en la BD (puede ser días/meses después de `dateTime`)

### Campos Meteorológicos

| Campo | Tipo | Propósito | Origen |
|-------|------|-----------|--------|
| **`weather_last_attempt`** | DateTimeField | Cuándo se intentó obtener datos meteorológicos por última vez | Sistema (al consultar OpenWeather) |

**Uso:**
- `weather_last_attempt`: Para control de reintentos de obtención de datos meteorológicos

---

## 📁 Track Model

Representa un archivo (CSV/GPX) con múltiples mediciones.

### Campos Temporales del Contenido

| Campo | Tipo | Propósito | Origen |
|-------|------|-----------|--------|
| **`start_time`** | DateTimeField | **Primera medición** del track (mínimo `dateTime` de las mediciones) | Calculado desde mediciones |
| **`end_time`** | DateTimeField | **Última medición** del track (máximo `dateTime` de las mediciones) | Calculado desde mediciones |

**Uso:**
- `start_time`: Cuándo empezó la recolección de datos en el track
- `end_time`: Cuándo terminó la recolección de datos en el track
- Representa el **rango temporal de las mediciones**, NO cuándo se subió el archivo

### Campos de Auditoría/Sistema

| Campo | Tipo | Propósito | Origen |
|-------|------|-----------|--------|
| **`created_at`** | DateTimeField (auto_now_add) | Cuándo se SUBIÓ el archivo al sistema | Django (automático) |
| **`updated_at`** | DateTimeField (auto_now) | Última modificación del registro Track | Django (automático) |

**Uso:**
- `created_at`: Timestamp de la carga del archivo (upload)
- `updated_at`: Última vez que se modificó el estado del track (ej: pending → completed)

---

## 🎯 Project Model

| Campo | Tipo | Propósito |
|-------|------|-----------|
| **`created_at`** | DateTimeField (auto_now_add) | Cuándo se creó el proyecto |
| **`updated_at`** | DateTimeField (auto_now) | Última modificación del proyecto |

---

## 🔍 Casos de Uso Comunes

### Escenario 1: Upload de Track con mediciones antiguas

```
Track uploaded: 2025-11-03 14:30:00
Track.created_at = 2025-11-03 14:30:00  ← Cuándo se subió
Track.start_time = 2024-11-03 10:00:00  ← Primera medición del CSV
Track.end_time   = 2024-11-03 10:09:00  ← Última medición del CSV

Measurements:
  dateTime    = 2024-11-03 10:00:00  ← Cuándo se TOMÓ la medición
  created_at  = 2025-11-03 14:30:00  ← Cuándo se GUARDÓ en la BD
```

### Escenario 2: Medición en tiempo real (desde dispositivo)

```
Measurement taken: 2025-11-03 15:00:00
Sent to API:       2025-11-03 15:00:05

Measurement:
  dateTime    = 2025-11-03 15:00:00  ← Cuándo se tomó
  created_at  = 2025-11-03 15:00:05  ← Cuándo llegó a la BD
  track       = NULL                 ← No pertenece a ningún track
```

### Escenario 3: Consulta de datos meteorológicos

```
Measurement:
  dateTime              = 2024-11-03 10:00:00  ← Fecha de la medición
  weather_last_attempt  = 2025-11-03 14:31:00  ← Cuándo se consultó OpenWeather
  weather_data          = {...}                ← Datos del clima en dateTime
```

---

## ⚠️ Importante

1. **`dateTime` es el campo principal** para análisis temporal de datos
2. **`created_at` es solo auditoría** del sistema
3. Los tracks pueden tener `start_time` muy anterior a `created_at` (datos históricos)
4. Siempre usar **timezone-aware datetimes** (con `timezone.now()` o `timezone.make_aware()`)

---

## 🔧 Best Practices

### ✅ Correcto
```python
from django.utils import timezone

# Crear medición con fecha actual
measurement.dateTime = timezone.now()

# Parsear fecha del CSV
dt = parse_datetime(timestamp_str)
if timezone.is_naive(dt):
    dt = timezone.make_aware(dt)
measurement.dateTime = dt
```

### ❌ Incorrecto
```python
from datetime import datetime

# ❌ NO usar datetime.now() - es naive
measurement.dateTime = datetime.now()

# ❌ NO confundir created_at con dateTime
measurement.created_at = parse_datetime(csv_timestamp)  # created_at es automático
```

---

## 📊 Índices de Base de Datos

Los siguientes campos tienen índices para consultas rápidas:

- `BaseMeasurement.dateTime` - Para filtros temporales
- `BaseMeasurement.project + dateTime` - Consultas por proyecto y fecha
- `BaseMeasurement.device + dateTime` - Consultas por dispositivo y fecha
- `BaseMeasurement.track` - Para recuperar mediciones de un track

---

**Última actualización:** 2025-11-03
