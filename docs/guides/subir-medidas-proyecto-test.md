# Subir y consultar medidas — proyecto **radiation-test (id = 3)** con `curl`

Guía operativa para el proyecto de pruebas, usando solo `curl` y la API REST de
OpenRed.

> ## 📌 El proyecto de test es el **`id = 3`** (`radiation-test`, tipo `radiation`)
>
> Úsalo **siempre** como `project=3` al subir, y como `?project=3` al consultar.

Estructura ya creada (los IDs concretos de hoy; abajo se explica cómo
redescubrirlos por la API):

| Entidad   | ID   | Nombre              | Notas |
|-----------|------|---------------------|-------|
| **Project**   | **`3`** | radiation-test  | privado (`is_public=false`), tipo `radiation` |
| Mission   | `6`  | Misión de prueba    | pertenece al proyecto **3** |
| Campaign  | `29` | Campaña de prueba   | **protegida por contraseña** |

> **Contraseña de la campaña 29:** `<contraseña_campaña>` (pídela al responsable del proyecto)
>
> La campaña está protegida: **al subir un track hay que asociarlo a la misión y
> a la campaña e incluir la contraseña**. El endpoint de subida JSON la valida.

---

## 0. Variables base

```bash
# Producción
export BASE_URL="https://api.open-red.es"
# (Alternativa local)  export BASE_URL="http://localhost:8000"

export EMAIL="francisco.sanz.g@gmail.com"
export PASSWORD="<tu_contraseña>"

# >>> El proyecto de test es SIEMPRE el id 3 <<<
export PROJECT_ID=3

# Misión y campaña (se pueden redescubrir, ver sección 2)
export MISSION_ID=6
export CAMPAIGN_ID=29
export CAMPAIGN_PASSWORD="<contraseña_campaña>"
```

---

## 1. Autenticación: obtener el token (solo para subir/borrar)

La API usa **Token auth**. La lectura es pública (no necesita token); el token
solo hace falta para **escribir** (subir tracks, borrar).

```bash
export TOKEN=$(curl -s -X POST "$BASE_URL/dj-rest-auth/login/" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['key'])")

echo "TOKEN=$TOKEN"
```

Cabecera para las peticiones autenticadas: `Authorization: Token <TOKEN>`.

---

## 2. Descubrir los IDs: proyecto, misiones y campañas (GET, público)

Todos estos endpoints son **públicos** (sin token). Las listas de misiones y
campañas son **anidadas** (no hay `/api/missions/` ni `/api/campaigns/` planos):
las misiones se piden por proyecto y las campañas por misión.

### a) Proyectos → localizar el `id = 3`

```bash
curl -s "$BASE_URL/api/projects/" | python3 -m json.tool
# Busca el que tiene  "id": 3, "name": "radiation-test"
```

Detalle de un proyecto concreto:

```bash
curl -s "$BASE_URL/api/projects/$PROJECT_ID/"
```
```json
{"id":3,"measurements_count":0,"last_measurement_date":null,
 "name":"radiation-test","project_type":"radiation","is_public":false, ...}
```

### b) Misiones **de un proyecto** → `GET /api/projects/{project_id}/missions/`

```bash
curl -s "$BASE_URL/api/projects/$PROJECT_ID/missions/"
```
```json
[{"id":6,"name":"Misión de prueba","start_date":"2026-05-25",
  "end_date":"2030-12-31","project":3,"created_by":2}]
```
→ el **`id` de cada objeto** es el `MISSION_ID`. Aquí, `6`.

### c) Campañas **de una misión** → `GET /api/missions/{mission_id}/campaigns/`

```bash
curl -s "$BASE_URL/api/missions/$MISSION_ID/campaigns/"
```
```json
[{"id":29,"has_password":true,"name":"Campaña de prueba",
  "mission":6,"participants":[]}]
```
→ el **`id`** es el `CAMPAIGN_ID` (aquí `29`). Fíjate en **`has_password`**:
si es `true`, esa campaña **exige contraseña** para subir tracks.

### d) Capturar los IDs en variables automáticamente

```bash
# Primera misión del proyecto 3
export MISSION_ID=$(curl -s "$BASE_URL/api/projects/$PROJECT_ID/missions/" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")

# Primera campaña de esa misión
export CAMPAIGN_ID=$(curl -s "$BASE_URL/api/missions/$MISSION_ID/campaigns/" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")

echo "PROJECT_ID=$PROJECT_ID  MISSION_ID=$MISSION_ID  CAMPAIGN_ID=$CAMPAIGN_ID"
# -> PROJECT_ID=3  MISSION_ID=6  CAMPAIGN_ID=29
```

> Detalle individual (sin lista): `GET /api/missions/{id}/` y
> `GET /api/campaigns/{id}/`.

---

## 3. (Opcional) Dispositivo

Para la subida JSON el dispositivo se identifica por su `id` (nº de serie / MAC)
dentro del propio JSON; si no existe **se crea automáticamente** y queda
asignado a tu usuario. No necesitas saber el ID numérico de antemano.

Listar dispositivos: `curl -s "$BASE_URL/api/devices/" -H "Authorization: Token $TOKEN"`

---

## 4. Formatos soportados

| Formato | Endpoint | Exige misión+campaña | Valida contraseña |
|---------|----------|:--------------------:|:-----------------:|
| **JSON** (RadiaCode app) | `POST /api/tracks/upload_json/` | **Sí** | **Sí** |
| **RCTRK** (export Android o iOS) | `POST /api/tracks/upload/` | **Sí** | **Sí** |

> Ambos exigen misión + campaña y validan la contraseña. Límites: 20 000 puntos / 10 MiB
> y 30 subidas por hora y usuario (`TRACK_UPLOAD_RATE`). El RCTRK admite el export de
> Android (texto tabulado) y el de iOS (JSON); se detecta solo.
>
> ```bash
> curl -X POST https://map.open-red.es/api/tracks/upload/ \
>   -H "Authorization: Token <token>" \
>   -F file=@ruta.rctrk -F device=56 -F mission=6 -F campaign=29 -F campaign_password=test1234
> ```
> Responde `202` con `track_id`; el procesado es asíncrono (`GET /api/tracks/{id}/` → `status`).

---

## 5. Preparar el track (JSON)

Campos clave de asignación, **a nivel raíz**: `project` (=3), `mission`,
`campaign`, `campaign_password`. Además `device` `{id, name}` y `points`.

```bash
cat > track_test.json <<JSON
{
  "name": "Prueba subida API",
  "description": "Track de ejemplo para el proyecto radiation-test",
  "trackType": "radiation",

  "project": $PROJECT_ID,
  "mission": $MISSION_ID,
  "campaign": $CAMPAIGN_ID,
  "campaign_password": "$CAMPAIGN_PASSWORD",

  "device": { "id": "52:43:06:60:1C:84", "name": "RadiaCode-102#RC-102-007300" },

  "startedAt": "2026-05-25T10:00:00",
  "endedAt": "2026-05-25T10:02:00",
  "requiredGpsAccuracyMeters": 10.0,

  "points": [
    {"timestamp":"2026-05-25T10:00:05","latitude":41.6488,"longitude":-0.8891,"altitude":200.0,"accuracyMeters":3.2,"cpm":25,"doseMicroSvPerHour":0.12},
    {"timestamp":"2026-05-25T10:00:35","latitude":41.6490,"longitude":-0.8895,"altitude":201.0,"accuracyMeters":2.8,"cpm":27,"doseMicroSvPerHour":0.13},
    {"timestamp":"2026-05-25T10:01:05","latitude":41.6492,"longitude":-0.8899,"altitude":202.0,"accuracyMeters":4.0,"cpm":24,"doseMicroSvPerHour":0.11}
  ]
}
JSON
```

> El servidor valida que `campaign` (29) pertenezca a `mission` (6) y que
> `mission.project` sea `project` (3). Si mezclas IDs de otro proyecto → 400.

---

## 6. Subir el track (JSON)

```bash
curl -s -X POST "$BASE_URL/api/tracks/upload_json/" \
  -H "Authorization: Token $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @track_test.json
```

Respuesta correcta (HTTP **202 Accepted**, procesado **asíncrono**):

```json
{ "track_id": 123, "job_id": "track_123", "status": "pending",
  "message": "Track ... Processing in background." }
```

```bash
export TRACK_ID=123
```

Errores: `400` (faltan IDs / falta contraseña / IDs incoherentes / sin puntos) ·
`403` (**contraseña incorrecta**) · `413` (demasiado grande) · `401` (sin token).

---

## 7. Consultar el estado del procesado

```bash
curl -s "$BASE_URL/api/tracks/$TRACK_ID/status/" -H "Authorization: Token $TOKEN"
```
```json
{ "track_id": 123, "status": "completed", "measurements_count": 3,
  "error_message": null, "start_time": "...", "end_time": "..." }
```
Estados: `pending` → `processing` → `completed` (o `failed`).

---

## 8. Consultar medidas (GET, público — sin token)

Cada medida lleva su propio `project_id`. Filtra directamente:

```bash
# Todas las medidas del proyecto 3
curl -s "$BASE_URL/api/radiation-measurements/?project=$PROJECT_ID"

# Solo las de la campaña 29
curl -s "$BASE_URL/api/radiation-measurements/?project=$PROJECT_ID&campaign=$CAMPAIGN_ID"
```

### Filtros disponibles (combinables)

| Param | Descripción |
|-------|-------------|
| `project` | ID de proyecto (**usa 3**) |
| `mission` / `campaign` / `track` / `device` | por ID |
| `start_date` / `end_date` | `YYYY-MM-DD` |
| `north`/`south`/`east`/`west` | bounding box (los 4 → índice espacial PostGIS) |
| `min_altitude`/`max_altitude` · `min_dose_rate`/`max_dose_rate` · `min_speed`/`max_speed` | rangos |

### Conteo y paginación (mismos filtros que la lista)

```bash
# Total filtrado
curl -s "$BASE_URL/api/radiation-measurements/count/?project=$PROJECT_ID"
# -> {"total": 0}   (radiation-test está vacío)

# Paginado (page_size por defecto 1000, máx 15000) — recomendado para grandes volúmenes
curl -s "$BASE_URL/api/radiation-measurements/paginated/?project=$PROJECT_ID&page=1&page_size=1000"
```

> La lista (`/`) filtra pero **no pagina** (devuelve todo). `count/` y
> `paginated/` aplican **los mismos filtros**.

### Tus tracks (esto sí requiere token)

```bash
curl -s "$BASE_URL/api/tracks/my_tracks/?project=$PROJECT_ID&campaign=$CAMPAIGN_ID" \
  -H "Authorization: Token $TOKEN"
```

---

## 9. Eliminar el track

```bash
curl -s -X DELETE "$BASE_URL/api/tracks/$TRACK_ID/" \
  -H "Authorization: Token $TOKEN" -o /dev/null -w "%{http_code}\n"
```

Respuesta **204**. ⚠️ Borrar el track **borra también todas sus medidas**
(`on_delete=CASCADE`). Solo el **creador** del track puede borrarlo.

---

## 10. Endpoints de escritura deshabilitados (temporalmente)

Para forzar que toda subida pase por una vía con contraseña, están
**deshabilitados** (responden `503` a usuarios autenticados):

- `POST /api/radiation-measurements/` y `POST /api/measurements/` (crear medida
  suelta) — no validaban la contraseña de campaña.

`POST /api/tracks/upload/` (RCTRK) estuvo deshabilitado por el mismo motivo hasta
2026-09-18; ahora exige misión + campaña, valida la contraseña y comparte el rate
limit con `upload_json`.

Reactivar: quitar el bloque `return ... 503 ...` marcado en `measures/views.py` y
`sudo supervisorctl restart openred-api-gunicorn`.

---

## Resumen de endpoints

| Acción | Método y ruta |
|--------|---------------|
| Login (token) | `POST /dj-rest-auth/login/` |
| Listar proyectos | `GET /api/projects/` |
| Detalle de proyecto | `GET /api/projects/3/` |
| **Misiones de un proyecto** | `GET /api/projects/3/missions/` |
| **Campañas de una misión** | `GET /api/missions/{mission_id}/campaigns/` |
| Detalle misión / campaña | `GET /api/missions/{id}/` · `GET /api/campaigns/{id}/` |
| Subir track JSON | `POST /api/tracks/upload_json/` |
| Estado del track | `GET /api/tracks/{id}/status/` |
| Mis tracks | `GET /api/tracks/my_tracks/` |
| Medidas (público, filtra) | `GET /api/radiation-measurements/?project=3` |
| Conteo / paginado | `GET /api/radiation-measurements/count/?project=3` · `.../paginated/?project=3` |
| Borrar track (+ medidas) | `DELETE /api/tracks/{id}/` |
| Subir RCTRK | `POST /api/tracks/upload/` (multipart: file, device, mission, campaign, campaign_password) |
| Crear medida suelta | _deshabilitado (503)_ |
| Docs interactivas | `GET /api/docs/` (Swagger) · `GET /api/redoc/` |

---

_Proyecto de pruebas: **`project = 3`** (`radiation-test`). Jerarquía:
`Project → Mission → Campaign → Track → Measurements`._
