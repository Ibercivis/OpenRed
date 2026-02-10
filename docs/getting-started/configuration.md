# Configuration

This guide covers OpenRed's configuration options and environment variables.

## Environment Variables

OpenRed uses environment variables for configuration. Copy `env_example` to `.env` and customize:

```bash
cp env_example .env
```

### Database Configuration

```bash
# PostgreSQL Database
DB_NAME=openred_db
DB_USER=openred_user
DB_PASSWORD=your_secure_password
DB_HOST=localhost
DB_PORT=5432
```

### Django Settings

```bash
# Secret key for Django (generate with: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')
SECRET_KEY=your-very-long-secret-key-here

# Debug mode (set to False in production!)
DEBUG=True

# Allowed hosts (comma-separated, no spaces)
ALLOWED_HOSTS=localhost,127.0.0.1,yourdomain.com
```

### Redis Configuration

```bash
# Redis for task queue and caching
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
```

### Email Configuration (Optional)

For sending notification emails:

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=noreply@openred.io
```

### OpenWeather API (Optional)

For automatic weather data fetching:

```bash
OPENWEATHER_API_KEY=your_api_key_here
```

Get your free API key at [OpenWeather](https://openweathermap.org/api).

### AWS S3 Configuration (Optional)

For storing uploaded files in S3:

```bash
USE_S3=True
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_STORAGE_BUCKET_NAME=your-bucket-name
AWS_S3_REGION_NAME=eu-west-1
```

## Django Settings

The main settings file is located at `openred/settings.py`.

### Key Settings

#### Database

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': os.getenv('DB_NAME', 'openred_db'),
        'USER': os.getenv('DB_USER', 'openred_user'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}
```

#### Redis Queue (RQ)

```python
RQ_QUEUES = {
    'default': {
        'HOST': os.getenv('REDIS_HOST', 'localhost'),
        'PORT': int(os.getenv('REDIS_PORT', 6379)),
        'DB': 0,
        'DEFAULT_TIMEOUT': 360,
    }
}
```

#### REST Framework

```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
}
```

#### CORS Settings

```python
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8080",
]

CORS_ALLOW_CREDENTIALS = True
```

## File Upload Settings

### Maximum Upload Size

Edit `openred/settings.py`:

```python
# Maximum upload size: 100MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100MB in bytes
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600
```

### Media Files

```python
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
```

Uploaded tracks are stored in `media/tracks/YYYY/MM/`.

## Project-Level Settings

Projects have a `project_settings` JSON field for custom configuration:

```python
from missions.models import Project

project = Project.objects.get(id=1)
project.project_settings = {
    'auto_weather_fetch': True,
    'measurement_threshold': 0.5,
    'notification_email': 'alerts@example.com',
    'custom_fields': {
        'location_name': 'required',
        'operator_name': 'optional'
    }
}
project.save()
```

## Logging Configuration

Configure logging in `openred/settings.py`:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'logs/openred.log',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': True,
        },
        'measures': {
            'handlers': ['file', 'console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
```

## Performance Tuning

### Database Connection Pooling

```python
DATABASES['default']['OPTIONS'] = {
    'connect_timeout': 10,
}
```

### RQ Worker Count

Run multiple workers for better throughput:

```bash
# Terminal 1: Track processing (critical)
python manage.py rqworker openred-tracks

# Terminal 2: Track processing (additional worker)
python manage.py rqworker openred-tracks

# Terminal 3: Weather data (secondary)
python manage.py rqworker openred-weather
```

### Caching

Enable Redis caching:

```python
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'redis://{REDIS_HOST}:{REDIS_PORT}/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}
```

## Security Settings

### Production Checklist

Before deploying to production:

```python
# Set in .env
DEBUG=False
SECRET_KEY=<generate-a-strong-random-key>
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

# Enable HTTPS
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True

# HSTS
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=True
```

### CORS Configuration

For production API access:

```python
CORS_ALLOWED_ORIGINS = [
    "https://yourdomain.com",
    "https://app.yourdomain.com",
]
```

## Testing Configuration

For running tests with specific settings:

```bash
# Use test settings
python manage.py test --settings=openred.settings_test
```

Create `openred/settings_test.py`:

```python
from .settings import *

# Use in-memory SQLite for faster tests
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Disable migrations for faster tests
class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None

MIGRATION_MODULES = DisableMigrations()
```

## Custom Configuration Example

Here's a complete `.env` file example for production:

```bash
# Database
DB_NAME=openred_prod
DB_USER=openred_prod_user
DB_PASSWORD=super_secure_password_123
DB_HOST=db.example.com
DB_PORT=5432

# Django
SECRET_KEY=django-insecure-production-key-here
DEBUG=False
ALLOWED_HOSTS=api.openred.io,www.openred.io

# Redis
REDIS_HOST=redis.example.com
REDIS_PORT=6379

# Email
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=SG.xxxxxxxxxxx
DEFAULT_FROM_EMAIL=noreply@openred.io

# AWS S3
USE_S3=True
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_STORAGE_BUCKET_NAME=openred-media
AWS_S3_REGION_NAME=eu-west-1

# OpenWeather
OPENWEATHER_API_KEY=abcdef1234567890
```

## Next Steps

- **[Quick Start Guide](quick-start.md)** - Create your first project
- **[Production Deployment](../deployment/production-setup.md)** - Deploy to production
- **[Docker Deployment](../deployment/docker.md)** - Use Docker containers
