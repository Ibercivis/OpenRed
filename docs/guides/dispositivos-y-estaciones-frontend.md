# Frontend: registrar dispositivos y asociarlos a estaciones (radiación)

Guía para el equipo de frontend de los dos flujos de "Mis dispositivos" y "Estaciones".
Ambos usan **auth de usuario** (`Authorization: Token <token_usuario>`).

> Contexto: los dispositivos headless (M5Stack) suben medidas de radiación una a una.
> El **token de ingesta** vive en el dispositivo y se usa desde el firmware (cabecera
> `X-Device-Token`), no desde el frontend. Que una medida sea *movement* o *static* lo
> decide si el dispositivo tiene (o no) una estación asociada — no lo decide el frontend.

---

## 1) Registrar un dispositivo (y obtener el ingest token)

### a) Modelos disponibles (para el desplegable)
```
GET /api/device-models/
```

### b) Crear el dispositivo
```
POST /api/devices/
Authorization: Token <token_usuario>
Content-Type: application/json

{
  "device_model": 1,                 // id de DeviceModel (requerido)
  "serial_number": "M5-001",         // requerido, único
  "calibration_date": "2026-05-30",  // opcional
  "is_active": true                  // opcional
}
```

**Respuesta 201 — ⚠️ punto crítico de UX:**
```json
{
  "id": 12,
  "device_model": 1,
  "serial_number": "M5-001",
  "owner": 3,
  "has_ingest_token": true,
  "ingest_token_created_at": "2026-05-30T17:00:00Z",
  "ingest_token": "Xk9...EN-CLARO..."
}
```
El campo **`ingest_token` se devuelve SOLO en esta respuesta, una única vez**. La UI debe
mostrarlo/permitir copiarlo en ese momento (es lo que se configura en el firmware). No se
puede recuperar luego; si se pierde, hay que **rotarlo**.

### c) Listar mis dispositivos
```
GET /api/devices/mine/              → los que tengo + los que he usado para medir
GET /api/devices/mine/?owned=true   → solo los que son míos
Authorization: Token <token_usuario>
```
Por defecto devuelve también los dispositivos **prestados**: aquellos con los que el usuario
ha subido medidas aunque el `owner` sea otra persona (p. ej. un RadiaCode compartido entre
varios voluntarios). Cada elemento incluye:

- `is_owner` (bool): si es `false`, la UI debe ocultar las acciones de propietario (rotar
  token, editar, asociar a estación); ese dispositivo pertenece a otro usuario.
- `last_measurement_at`: fecha de la última medida del dispositivo (de cualquier usuario),
  o `null` si nunca ha medido.

No incluye el token; solo `has_ingest_token` (bool) y `ingest_token_created_at`.

### d) Rotar el token (perdido / reflasheo)
```
POST /api/devices/{id}/regenerate_token/
Authorization: Token <token_usuario>

→ { "ingest_token": "nuevo-token", "ingest_token_created_at": "..." }   // una sola vez
```

---

## 2) Asociar el dispositivo a una estación estática (solo estaciones fijas)

La asociación se hace **al crear la estación**, en el campo `device` (relación 1:1).

```
POST /api/stations/
Authorization: Token <token_usuario>
Content-Type: application/json

{
  "name": "Estación Tejado IES X",
  "device": 12,                      // id de un dispositivo TUYO, sin estación previa
  "project": 3,                      // id de proyecto de RADIACIÓN
  "latitude": 41.65,                 // ubicación fija (requerida)
  "longitude": -0.88,
  "altitude": 250,                   // opcional
  "expected_interval_seconds": 300   // opcional, default 300 (5 min)
}
```

**Read-only** (no enviar; los gestiona el servidor): `user`, `status`, `last_measurement_at`,
`created_at`, `updated_at`.

**Errores 400 a mostrar al usuario:**
- `"Este dispositivo no te pertenece."`
- `"Este dispositivo ya tiene una estación asociada."` (1:1)
- `"Una estación solo puede asociarse a un proyecto de radiación."`

**Gestión posterior:**
```
GET    /api/stations/mine/     → mis estaciones (usa status y last_measurement_at para pintar estado)
PATCH  /api/stations/{id}/      → editar (incl. cambiar 'device')
DELETE /api/stations/{id}/
```

---

## Notas

- **Orden en la UI:** 1) crear dispositivo y guardar el token; 2) si es estación fija, crear
  la estación eligiendo ese dispositivo + proyecto de radiación + ubicación.
- El **token es del dispositivo**, no de la estación. Cambiar el `device` de una estación
  **no** requiere regenerar token (el nuevo dispositivo ya trae el suyo).
- El frontend **no interviene en la subida de medidas**: la hace el aparato con su token en
  `X-Device-Token` contra `POST /api/ingest/radiation/`. La estación (en static) y la
  identidad del dispositivo las deriva el servidor del token; el firmware nunca manda
  `station_id` ni `device`.
