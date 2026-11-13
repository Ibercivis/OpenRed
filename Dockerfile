# OpenRed, containerised for local development. See LOCAL-DEV.md.
#
# This image exists on the `federation` branch only. OpenRed is not containerised
# in production — it runs under gunicorn + supervisor on a VM (see gunicorn.conf.py
# and deploy/) — and nothing here proposes that it should be.
#
# Python 3.11: Django 4.2 supports 3.8-3.12, and pandas 2.1.2 / numpy 1.26.1 have
# wheels for 3.11.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System libraries, in three groups:
#   * GDAL/GEOS/PROJ      - GeoDjango needs them at runtime. measures/models.py has
#                           three PointFields and the postgis backend loads libgdal
#                           and libgeos_c by name.
#   * pango/cairo/harfbuzz - WeasyPrint renders the PDF reports with them.
#   * postgresql-client    - only so `psql` and `pg_isready` are available for
#                            debugging; the app itself talks to the DB via psycopg2.
RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils \
        gdal-bin \
        libgdal32 \
        libgeos-c1v5 \
        libproj25 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libffi8 \
        fonts-dejavu-core \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY local_dev/requirements.txt /app/local_dev/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/local_dev/requirements.txt

COPY local_dev/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY . /app

# matplotlib writes a font cache; give it a writable home.
ENV MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["api"]
