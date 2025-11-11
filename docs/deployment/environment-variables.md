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
