#!/bin/sh
# Entrypoint for the local development container. One image, four roles, chosen by the
# first argument, because the three RQ queues in openred/settings.py each want
# their own worker and rq-scheduler runs on its own (deploy/supervisor/ does the
# same thing with supervisor programs on the production VM).
set -e

wait_for_db() {
    echo "waiting for postgres at ${DB_HOST}:${DB_PORT} ..."
    until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -q; do
        sleep 1
    done
    echo "postgres is up"
}

case "$1" in
  api)
    wait_for_db
    python manage.py migrate --noinput
    # collectstatic so /admin/ has its CSS. STATICFILES_DIRS names BASE_DIR/static,
    # which does not exist in the repository, so create it rather than let
    # collectstatic fail on a missing directory.
    mkdir -p /app/static
    python manage.py collectstatic --noinput --clear >/dev/null
    exec python manage.py runserver 0.0.0.0:8000
    ;;
  worker)
    wait_for_db
    shift
    exec python manage.py rqworker "$@"
    ;;
  scheduler)
    wait_for_db
    # The queue name is required. `manage.py rqscheduler` with no --queue
    # defaults to a queue called "default", which is not in RQ_QUEUES, and dies
    # with KeyError: 'default'. Production passes openred-weather
    # (deploy/supervisor/openred-rq-scheduler.conf) because that is the
    # connection init_rq_scheduler schedules the recurring jobs on.
    #
    # The recurring jobs themselves are NOT registered here. Doing so would make
    # a local stack start calling Open-Meteo and reverse-geocoding on a timer.
    # To register them deliberately:
    #     docker compose exec api python manage.py init_rq_scheduler
    exec python manage.py rqscheduler --queue "${RQ_SCHEDULER_QUEUE:-openred-weather}"
    ;;
  *)
    exec "$@"
    ;;
esac
