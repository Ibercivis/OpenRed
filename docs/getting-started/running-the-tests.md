# Running the Tests

The suite lives in `devices/`, `measures/`, `missions/` and `users/`, and `manage.py test` needs
three things that are easy to miss: a **PostGIS** database, a **Redis**, and two settings that have
no default. `compose.test.yaml` provides the first two with the same images the CI job uses, so a
pass on your machine is a pass in CI.

## Once, per machine

GeoDjango loads GEOS, GDAL and PROJ when Django starts, `psycopg2` is built from source, and
WeasyPrint — which the PDF reports import — needs Pango:

```bash
sudo apt-get install -y binutils gdal-bin libgdal-dev libgeos-dev \
                        libpango-1.0-0 libpangoft2-1.0-0 libpq-dev libproj-dev
```

On macOS, `brew install gdal geos proj libpq pango` is the equivalent.

## Every time

```bash
# 1. The database and the queue. --wait holds until both report healthy.
docker compose -f compose.test.yaml up -d --wait

# 2. The suite. The database settings all default to what the compose file
#    exposes, so only these three are needed.
SECRET_KEY=dev-only-not-a-secret \
FRONTEND_URL=http://localhost:3000 \
SECURE_SSL_REDIRECT=False \
python manage.py test

# 3. Afterwards. -v removes the volumes, so the next run starts clean.
docker compose -f compose.test.yaml down -v
```

A green run says:

```text
Ran 71 tests in 9s

OK (skipped=4, expected failures=1)
```

### Why those three settings

| | |
|---|---|
| `SECRET_KEY` | The only `config()` call in `settings.py` with no default at all |
| `FRONTEND_URL` | Read in the `DEBUG=False` branch, for `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` |
| `SECURE_SSL_REDIRECT` | `DEBUG=False` turns on the production security block, which otherwise redirects the test client to https and answers `301` to most of the suite |

A `.env` file works just as well — an exported variable wins over it, so the command above
overrides whatever you develop against.

## Running less than everything

```bash
python manage.py test measures                     # one app
python manage.py test measures.test_h3             # one module
python manage.py test users.tests.TokenManagementTestCase.test_token_is_created_on_login
python manage.py test -v 2                         # name every test as it runs
```

**Careful with single tests.** Running one test at a time hides failures that only a whole run
produces: a leaked transaction, for instance, shows up as `connection already closed` in every
*later* test and in none on its own. Before pushing, run the suite whole.

## The H3 tests skip, and that is expected

The ten tests in `measures/test_h3.py` cover the aggregation endpoints, which call
`h3_lat_lng_to_cell()` in SQL. That function comes from **h3-pg**, a PostgreSQL extension, and not
from the `h3` Python package in `requirements.txt`. Debian packages h3-pg nowhere — it has to be
compiled from source — so neither the image above nor any other ready-made one carries it, and
those tests skip themselves with that reason. It is the same accommodation
`measures/migrations/0002_enable_h3_extension.py` makes: a database without the extension runs the
whole application except those endpoints.

To run them, point the suite at a database that does have the extension, by exporting `DB_HOST`,
`DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` for it.

## If the first run fails with `connection already closed`

The PostgreSQL entrypoint starts a temporary server to run its initialisation, then restarts into
the real one; a connection made in between is dropped. `--wait` normally outlasts it. If it does
not, run the suite again — the database is initialised by then, and `down -v` is what resets it.
