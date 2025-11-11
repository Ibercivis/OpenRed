# Production Deployment Guide

This guide covers deploying OpenRed API to a production Ubuntu/Debian server with PostgreSQL, PostGIS, Nginx, and Gunicorn.

## Prerequisites

- Ubuntu 20.04+ or Debian 11+ server
- Root or sudo access
- Domain name pointing to your server (e.g., api.open-red.es)
- PostgreSQL 12+ with PostGIS extension
- Python 3.9+

## Architecture Overview

```
Internet → Nginx (reverse proxy) → Gunicorn (WSGI server) → Django app
                                  → RQ Worker (background tasks)
                                  → Redis (task queue)
```

## 1. System Dependencies

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install Python and build tools
sudo apt install -y python3.11 python3.11-venv python3-pip python3-dev

# Install PostgreSQL + PostGIS
sudo apt install -y postgresql postgresql-contrib postgis

# Install Redis for RQ task queue
sudo apt install -y redis-server

# Install Nginx
sudo apt install -y nginx

# Install system libraries for geospatial processing
sudo apt install -y libgdal-dev libgeos-dev libproj-dev
sudo apt install -y binutils gdal-bin
```

## 2. PostgreSQL + PostGIS Setup

```bash
# Switch to postgres user
sudo -u postgres psql

# Inside PostgreSQL prompt:
CREATE DATABASE openred_db;
CREATE USER openred_user WITH PASSWORD 'your_secure_password_here';

-- Grant privileges
ALTER ROLE openred_user SET client_encoding TO 'utf8';
ALTER ROLE openred_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE openred_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE openred_db TO openred_user;

-- Connect to database and enable PostGIS
\c openred_db
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS h3;
CREATE EXTENSION IF NOT EXISTS h3_postgis;

-- Grant schema permissions
GRANT ALL ON SCHEMA public TO openred_user;

\q
```

Verify PostGIS installation:
```bash
sudo -u postgres psql -d openred_db -c "SELECT PostGIS_version();"
```

## 3. Application Setup

```bash
# Create application user
sudo useradd -m -s /bin/bash openred
sudo su - openred

# Clone repository (adjust URL)
git clone https://github.com/Ibercivis/OpenRed.git openred-api
cd openred-api

# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

## 4. Environment Configuration

Create production environment file:

```bash
cd /home/openred/openred-api
nano .env
```

Copy from `.env.example` and configure (see [Environment Variables](environment-variables.md) for full details):

```bash
# Django Settings
DEBUG=False
SECRET_KEY=your_production_secret_key_here  # Generate with Django
ALLOWED_HOSTS=api.open-red.es,yourdomain.com

# Database
DB_NAME=openred_db
DB_USER=openred_user
DB_PASSWORD=your_secure_password_here
DB_HOST=localhost
DB_PORT=5432

# Email (AWS SES)
EMAIL_BACKEND=django_ses.SESBackend
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
AWS_SES_REGION_NAME=eu-west-1
AWS_SES_REGION_ENDPOINT=email.eu-west-1.amazonaws.com
DEFAULT_FROM_EMAIL=noreply@ibercivis.es

# Frontend URL (for password reset emails)
FRONTEND_URL=https://map.open-red.es

# APIs
OSR_API_KEY=your_openweathermap_key
MAPBOX_ACCESS_TOKEN=your_mapbox_token

# Redis for RQ
REDIS_URL=redis://localhost:6379/0

# Security
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

**Generate SECRET_KEY:**
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## 5. Django Setup

```bash
# As openred user with venv activated
cd /home/openred/openred-api
source .venv/bin/activate

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic --noinput

# Test Gunicorn
gunicorn openred.wsgi:application --bind 127.0.0.1:8000 --timeout 120
# Press Ctrl+C after verifying it works
```

## 6. Systemd Service for Gunicorn

Create systemd service file:

```bash
sudo nano /etc/systemd/system/openred-gunicorn.service
```

```ini
[Unit]
Description=OpenRed Gunicorn WSGI Server
After=network.target postgresql.service

[Service]
Type=notify
User=openred
Group=openred
RuntimeDirectory=gunicorn
WorkingDirectory=/home/openred/openred-api
Environment="PATH=/home/openred/openred-api/.venv/bin"
ExecStart=/home/openred/openred-api/.venv/bin/gunicorn \
    --workers 4 \
    --worker-class gthread \
    --threads 2 \
    --timeout 120 \
    --bind unix:/run/gunicorn/openred.sock \
    --access-logfile /home/openred/openred-api/logs/gunicorn-access.log \
    --error-logfile /home/openred/openred-api/logs/gunicorn-error.log \
    --log-level info \
    openred.wsgi:application
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Create log directory:
```bash
sudo mkdir -p /home/openred/openred-api/logs
sudo chown -R openred:openred /home/openred/openred-api/logs
```

Enable and start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable openred-gunicorn
sudo systemctl start openred-gunicorn
sudo systemctl status openred-gunicorn
```

## 7. Systemd Service for RQ Worker

Background task processing for file uploads:

```bash
sudo nano /etc/systemd/system/openred-rqworker.service
```

```ini
[Unit]
Description=OpenRed RQ Worker
After=network.target redis.service

[Service]
Type=simple
User=openred
Group=openred
WorkingDirectory=/home/openred/openred-api
Environment="PATH=/home/openred/openred-api/.venv/bin"
ExecStart=/home/openred/openred-api/.venv/bin/python manage.py rqworker default
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable openred-rqworker
sudo systemctl start openred-rqworker
sudo systemctl status openred-rqworker
```

## 8. Nginx Configuration

Create Nginx site configuration:

```bash
sudo nano /etc/nginx/sites-available/openred-api
```

```nginx
upstream openred_app {
    server unix:/run/gunicorn/openred.sock fail_timeout=0;
}

server {
    listen 80;
    server_name api.open-red.es;
    
    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.open-red.es;
    
    # SSL certificates (use Let's Encrypt - see below)
    ssl_certificate /etc/letsencrypt/live/api.open-red.es/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.open-red.es/privkey.pem;
    
    # SSL security settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    
    # Logs
    access_log /var/log/nginx/openred-access.log;
    error_log /var/log/nginx/openred-error.log;
    
    # Max upload size (for track files)
    client_max_body_size 50M;
    
    # Static files
    location /static/ {
        alias /home/openred/openred-api/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    
    # Media files (uploaded tracks)
    location /media/ {
        alias /home/openred/openred-api/media/;
        expires 7d;
    }
    
    # Proxy to Gunicorn
    location / {
        proxy_pass http://openred_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        
        # Timeout for long-running requests
        proxy_connect_timeout 75s;
        proxy_read_timeout 120s;
    }
}
```

Enable site and restart Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/openred-api /etc/nginx/sites-enabled/
sudo nginx -t  # Test configuration
sudo systemctl restart nginx
```

## 9. SSL Certificate (Let's Encrypt)

```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain certificate (Nginx plugin handles configuration)
sudo certbot --nginx -d api.open-red.es

# Test auto-renewal
sudo certbot renew --dry-run

# Certificates auto-renew via systemd timer
sudo systemctl status certbot.timer
```

## 10. Firewall Configuration

```bash
# Enable UFW firewall
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable

# Check status
sudo ufw status
```

## 11. Monitoring and Maintenance

### Check Service Status

```bash
# Gunicorn
sudo systemctl status openred-gunicorn
sudo journalctl -u openred-gunicorn -f  # Follow logs

# RQ Worker
sudo systemctl status openred-rqworker
sudo journalctl -u openred-rqworker -f

# Nginx
sudo systemctl status nginx
sudo tail -f /var/log/nginx/openred-error.log
```

### Application Logs

```bash
# Gunicorn logs
tail -f /home/openred/openred-api/logs/gunicorn-error.log

# Django application logs (if configured)
tail -f /home/openred/openred-api/logs/django.log
```

### Update Application

```bash
# As openred user
cd /home/openred/openred-api
git pull origin main

# Activate venv and update dependencies
source .venv/bin/activate
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput

# Restart services
sudo systemctl restart openred-gunicorn
sudo systemctl restart openred-rqworker
```

### Database Backup

```bash
# Backup database
sudo -u postgres pg_dump openred_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore from backup
sudo -u postgres psql openred_db < backup_20240315_120000.sql
```

## 12. Performance Tuning

### PostgreSQL

Edit `/etc/postgresql/*/main/postgresql.conf`:

```ini
shared_buffers = 256MB  # 25% of RAM
effective_cache_size = 1GB  # 50% of RAM
work_mem = 16MB
maintenance_work_mem = 128MB
checkpoint_completion_target = 0.9
```

Restart PostgreSQL:
```bash
sudo systemctl restart postgresql
```

### Gunicorn Workers

Calculate workers: `(2 × CPU cores) + 1`

For 4 CPU cores: `--workers 9`

## Security Checklist

- [ ] `DEBUG=False` in production
- [ ] Strong `SECRET_KEY` generated
- [ ] `ALLOWED_HOSTS` configured correctly
- [ ] SSL certificate installed and auto-renewing
- [ ] Firewall enabled (UFW)
- [ ] Database user has strong password
- [ ] AWS SES credentials secured in `.env`
- [ ] `.env` file permissions: `chmod 600 .env`
- [ ] Regular security updates: `sudo apt update && sudo apt upgrade`
- [ ] Django Site domain configured: `http://development.ibercivis.es:8000` (dev) or `https://api.open-red.es` (prod)

## Troubleshooting

### 502 Bad Gateway

Check Gunicorn is running:
```bash
sudo systemctl status openred-gunicorn
```

Check socket permissions:
```bash
ls -la /run/gunicorn/openred.sock
```

### 500 Internal Server Error

Check Django logs:
```bash
tail -f /home/openred/openred-api/logs/gunicorn-error.log
```

### RQ Jobs Not Processing

Check worker status:
```bash
sudo systemctl status openred-rqworker
```

Check Redis connection:
```bash
redis-cli ping  # Should return "PONG"
```

### Database Connection Errors

Verify PostgreSQL is running:
```bash
sudo systemctl status postgresql
```

Test connection:
```bash
sudo -u openred psql -h localhost -U openred_user -d openred_db
```

## Additional Resources

- [Environment Variables Configuration](environment-variables.md)
- [Email Setup with AWS SES](email-setup.md)
- [Django Deployment Checklist](https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/)
- [Gunicorn Documentation](https://docs.gunicorn.org/)
- [Nginx Documentation](https://nginx.org/en/docs/)
