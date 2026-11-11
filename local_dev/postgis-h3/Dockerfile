# PostgreSQL with BOTH extensions OpenRed's SQL needs: PostGIS and h3.
#
# Why this image exists instead of `postgis/postgis`
# --------------------------------------------------
# OpenRed calls the server-side function `h3_lat_lng_to_cell` in raw SQL, in
# `measures/views.py`, for three things:
#
#   * GET /api/radiation-measurements/h3-aggregation/
#   * GET /api/radiation-measurements/h3-aggregation-vertex/   (and the
#     light-pollution equivalents)
#   * GET /api/{radiation,light-pollution}-measurements/report/  -- the PDF
#     report, which the frontend does call
#
# So h3-pg has to be installed in the database, and it is not in the
# `postgis/postgis` images. Two things rule out simply adding it to one:
#
#   * every `postgis/postgis` tag is Debian **bullseye**, and
#     apt.postgresql.org no longer serves a `bullseye-pgdg` Release file, so
#     `apt-get update` fails outright inside those images; and
#   * they publish **amd64 only**, so on an arm64 laptop they run emulated.
#
# Starting from the official `postgres:17-bookworm` instead fixes both: pgdg
# serves bookworm, and that image is multi-arch, so this runs natively.
#
# Everything else in OpenRed does its H3 work in Python with the `h3` package
# (`stats/heatmap`, `activity_digest`), which is why migration 0002 tolerates the
# extension being absent -- a database without it still runs the whole
# application except the endpoints listed above.
FROM postgres:17-bookworm

ENV POSTGIS_MAJOR=3

# PostGIS from pgdg (already configured in this image's sources), then h3-pg
# compiled from source because Debian does not package it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        "postgresql-$PG_MAJOR-postgis-$POSTGIS_MAJOR" \
        "postgresql-$PG_MAJOR-postgis-$POSTGIS_MAJOR-scripts" \
        build-essential \
        cmake \
        git \
        ca-certificates \
        "postgresql-server-dev-$PG_MAJOR" \
    # h3-pg publishes no git tags at all -- `git ls-remote` shows only
    # refs/heads/main -- so this pins the exact commit rather than a version.
    && git clone --recurse-submodules \
         https://github.com/zachasme/h3-pg.git /tmp/h3-pg \
    && git -C /tmp/h3-pg checkout 04227cb62a7338eb2a655fff875b125de2f043f4 \
    && git -C /tmp/h3-pg submodule update --init --recursive \
    # h3-pg's CMake adds `format_h3` / `format_h3_postgis` targets to `all` when
    # it finds pgindent, and they fail here ("Use of uninitialized value
    # $typedefs_file ... pgindent line 137") because pgindent wants a typedefs
    # list that is not shipped. They only reformat source. Hiding pgindent from
    # the configure step makes CMake leave those targets out, so the build
    # produces the extension and nothing else.
    && mv /usr/lib/postgresql/"$PG_MAJOR"/bin/pgindent \
          /usr/lib/postgresql/"$PG_MAJOR"/bin/pgindent.unused \
    && cmake -S /tmp/h3-pg -B /tmp/h3-pg/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/h3-pg/build --parallel "$(nproc)" \
    && cmake --install /tmp/h3-pg/build \
    && rm -rf /tmp/h3-pg \
    && apt-get purge -y build-essential cmake git "postgresql-server-dev-$PG_MAJOR" \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# `postgis/postgis` enables its extensions through an init script; this image has
# none, and OpenRed has no CreateExtension migration for PostGIS either, so both
# extensions are created here on first initialisation of the data directory.
COPY enable-extensions.sh /docker-entrypoint-initdb.d/10-enable-extensions.sh
