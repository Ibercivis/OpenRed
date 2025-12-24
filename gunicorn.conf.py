# Gunicorn configuration for OpenRed API
# Path: /home/ubuntu/openred-api/gunicorn.conf.py

import multiprocessing
import os

# Server socket
bind = "unix:/home/ubuntu/openred-api/gunicorn.sock"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 30
keepalive = 2

# Restart workers after this many seconds, randomly
max_worker_lifetime = 3600
graceful_timeout = 30

# Logging
loglevel = "info"
accesslog = "/var/log/gunicorn/access.log"
errorlog = "/var/log/gunicorn/error.log"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'openred-api'

# Server mechanics
preload_app = True
pidfile = "/var/run/openred/gunicorn.pid"
user = "ubuntu"
group = "ubuntu"
tmp_upload_dir = None

# SSL (if needed later)
# keyfile = "/path/to/ssl/private.key"
# certfile = "/path/to/ssl/certificate.crt"

# Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openred.settings")

# Application
wsgi_app = "openred.wsgi:application"

# Worker timeout
worker_tmp_dir = "/dev/shm"
