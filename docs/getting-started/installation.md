# Installation Guide

This guide will help you set up OpenRed on your local machine or server.

## Prerequisites

Before installing OpenRed, ensure you have:

- **Python 3.8+** installed
- **PostgreSQL 12+** with PostGIS extension
- **Redis 6+** for task queue
- **Git** for version control

## Step 1: Clone the Repository

```bash
git clone https://github.com/Ibercivis/OpenRed.git
cd OpenRed
```

## Step 2: Create Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 4: Configure Environment Variables

Copy the example environment file and edit it:

```bash
cp env_example .env
```

Edit `.env` with your configuration:

```bash
# Database Configuration
DB_NAME=openred_db
DB_USER=openred_user
DB_PASSWORD=your_secure_password
DB_HOST=localhost
DB_PORT=5432

# Django Secret Key
SECRET_KEY=your-secret-key-here

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379

# OpenWeather API (optional, for weather data)
OPENWEATHER_API_KEY=your_api_key_here

# Email Configuration (optional)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
```

## Step 5: Configure Database

Create a PostgreSQL database with PostGIS:

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE openred_db;
CREATE USER openred_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE openred_db TO openred_user;

-- Connect to the database
\c openred_db

-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS h3;
CREATE EXTENSION IF NOT EXISTS h3_postgis;

\q
```

## Step 6: Run Migrations

```bash
python manage.py migrate
```

You should see output like:

```
Running migrations:
  Applying contenttypes.0001_initial... OK
  Applying auth.0001_initial... OK
  Applying devices.0001_initial... OK
  Applying missions.0001_initial... OK
  Applying measures.0001_initial... OK
  ...
```

## Step 7: Create Superuser

```bash
python manage.py createsuperuser
```

Follow the prompts to create an admin account.

## Step 8: Collect Static Files (Production only)

```bash
python manage.py collectstatic
```

## Step 9: Start Services

You need to run three services:

### Terminal 1: Django Development Server

```bash
python manage.py runserver 0.0.0.0:8000
```

### Terminal 2: RQ Worker (Tracks)

```bash
python manage.py rqworker openred-tracks
```

### Terminal 3: Redis Server

```bash
redis-server
```

!!! tip "Using tmux or screen"
    For better terminal management, consider using `tmux` or `screen`:
    
    ```bash
    tmux new-session -d -s django 'python manage.py runserver'
    tmux new-session -d -s rqworker-tracks 'python manage.py rqworker openred-tracks'
    tmux new-session -d -s rqworker-weather 'python manage.py rqworker openred-weather'
    ```

## Verify Installation

1. **Admin Panel**: Open `http://localhost:8000/admin/` and login with your superuser credentials
2. **API Root**: Open `http://localhost:8000/api/` to see available endpoints
3. **Swagger UI**: Open `http://localhost:8000/swagger/` for interactive API documentation
4. **ReDoc**: Open `http://localhost:8000/redoc/` for alternative API docs

## Troubleshooting

### Database Connection Error

If you see `FATAL: database "openred_db" does not exist`:

```bash
sudo -u postgres createdb openred_db
```

### Redis Connection Error

If you see `Error 111 connecting to localhost:6379. Connection refused`:

```bash
# Start Redis
redis-server

# Or on Ubuntu/Debian with systemd
sudo systemctl start redis-server
```

### PostGIS Extension Error

If migrations fail with PostGIS errors:

```bash
sudo apt-get install postgresql-12-postgis-3  # Ubuntu/Debian
# or
brew install postgis  # macOS
```

### Permission Denied on PostgreSQL

```bash
sudo -u postgres psql openred_db
GRANT ALL PRIVILEGES ON DATABASE openred_db TO openred_user;
ALTER USER openred_user CREATEDB;
```

## Next Steps

- ✅ Installation complete!
- → Continue to **[Quick Start Guide](quick-start.md)** to create your first project
- → Read about **[Configuration](configuration.md)** for advanced settings
- → Learn the **[Data Hierarchy](../architecture/data-hierarchy.md)**

## Production Deployment

For production deployment, see:

- **[Production Deployment Guide](../deployment/production-setup.md)**
- **[Docker Deployment](../deployment/docker.md)**
