#!/bin/bash
# Create the extensions OpenRed's SQL needs, in the application database and in
# template1 so that a database created later (a test database, for instance)
# inherits them.
#
# PostGIS: measures/models.py has three PointFields and the first migration
# would fail without it. OpenRed has no CreateExtension migration -- in
# production a superuser enabled it by hand -- so it is done here.
#
# h3: needed by the raw SQL behind the h3-aggregation endpoints and the PDF
# report. See the Dockerfile for the list.
set -e

for db in "$POSTGRES_DB" template1; do
    echo "enabling postgis and h3 in $db"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<-SQL
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE EXTENSION IF NOT EXISTS h3;
        CREATE EXTENSION IF NOT EXISTS h3_postgis CASCADE;
SQL
done
