# OpenRed API - Environment Configuration Guide

## 📁 Archivos de configuración

- **`.env.example`** - Template con todas las variables documentadas
- **`.env.dev`** - Configuración de desarrollo (local)
- **`.env.prod`** - Configuración de producción (servidor)
- **`.env`** - Archivo activo (NO committear a git)

## 🚀 Cómo usar

### **Desarrollo local:**

```bash
# Copiar archivo de desarrollo
cp .env.dev .env

# Cargar variables de entorno
export $(cat .env | xargs)

# O cargar y ejecutar en una línea
export $(cat .env.dev | xargs) && python manage.py runserver
```

### **Producción:**

```bash
# Copiar archivo de producción
cp .env.prod .env

# ⚠️ IMPORTANTE: Editar .env y cambiar las credenciales
nano .env

# Cambiar:
# - SECRET_KEY (generar una nueva)
# - AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY
# - DB_PASSWORD (si es diferente)
# - OSR_API_KEY y MAPBOX_ACCESS_TOKEN

# Cargar variables de entorno
export $(cat .env | xargs)

# Reiniciar servicios
sudo supervisorctl restart openred-gunicorn
sudo supervisorctl restart openred-rqworker
```

## 🔑 Generar SECRET_KEY

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

## 📋 Variables principales

### **Desarrollo vs Producción**

| Variable | Desarrollo | Producción |
|----------|-----------|------------|
| `DEBUG` | `True` | `False` |
| `FRONTEND_URL` | `http://localhost:3000` | `https://map.open-red.es` |
| `EMAIL_BACKEND` | `console.EmailBackend` | `django_ses.SESBackend` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | `api.open-red.es` |
| `SECURE_SSL_REDIRECT` | `False` | `True` |

### **CORS en producción**

En producción, solo se permite el frontend oficial:
- `FRONTEND_URL=https://map.open-red.es`

CORS solo permitirá requests desde ese dominio.

### **Emails**

En desarrollo, los emails se imprimen en consola.
En producción, se envían via AWS SES desde `noreply@ibercivis.es`.

### **Informes PDF**

`REPORT_DEFAULT_LANGUAGE` (por defecto `en`) fija el idioma de los informes PDF
cuando la petición no trae `?lang=` ni cabecera `Accept-Language` soportada.
Valores admitidos: `en`, `es`. Tras desplegar hay que ejecutar
`python manage.py compilemessages -l es` para que exista la traducción al español.

`OSM_TILE_USER_AGENT` identifica la app ante los servidores de tiles de
OpenStreetMap al dibujar el mapa del informe. Sin un User-Agent identificativo
OSM devuelve tiles de "Access blocked" (403). Debe incluir nombre de la app y un
contacto, según la [política de uso de tiles de OSM](https://operations.osmfoundation.org/policies/tiles/).

### **Subida de tracks**

| Variable | Default | Descripción |
|---|---|---|
| `TRACK_UPLOAD_MAX_POINTS` | `20000` | Máximo de puntos por track (JSON y RCTRK). Se comprueba al recibir el JSON y al parsear el RCTRK. |
| `TRACK_UPLOAD_MAX_BYTES` | `10485760` (10 MiB) | Tamaño máximo del fichero o payload. |
| `TRACK_UPLOAD_RATE` | `30/hour` | Subidas por usuario autenticado en `POST /api/tracks/upload/` y `upload_json/` (DRF `ScopedRateThrottle`, scope `track_upload`). El contador vive en Redis (`CACHES`, misma DB que RQ), compartido entre workers de gunicorn. |

## 🔒 Seguridad

**NUNCA** commitear archivos `.env` con credenciales reales a git.

El `.gitignore` ya está configurado para excluir:
```
.env
.env.local
.env.*.local
```

## 🔄 Cambiar de entorno

```bash
# Cambiar a desarrollo
cp .env.dev .env
sudo supervisorctl restart all

# Cambiar a producción
cp .env.prod .env
sudo supervisorctl restart all
```

## 📝 Checklist antes de ir a producción

- [ ] Cambiar `SECRET_KEY` por una generada aleatoriamente
- [ ] Configurar `AWS_ACCESS_KEY_ID` y `AWS_SECRET_ACCESS_KEY`
- [ ] Verificar `FRONTEND_URL=https://map.open-red.es`
- [ ] Verificar `DEBUG=False`
- [ ] Configurar API keys reales (`OSR_API_KEY`, `MAPBOX_ACCESS_TOKEN`)
- [ ] Configurar contraseña segura de base de datos
- [ ] Verificar que `.env` NO esté en git (`git status`)

## 🧪 Verificar configuración

```bash
# Test que Django carga las variables correctamente
python manage.py check

# Ver valor de una variable específica
python manage.py shell -c "from django.conf import settings; print(settings.FRONTEND_URL)"

# Ver si DEBUG está activo
python manage.py shell -c "from django.conf import settings; print(f'DEBUG: {settings.DEBUG}')"
```
