#!/usr/bin/env sh
set -e

# Run DB migrations if alembic.ini exists
if [ -f "/app/alembic.ini" ]; then
    echo "Running database migrations..."
    alembic upgrade head
fi

echo "Starting gunicorn..."
exec gunicorn --config /app/gunicorn.conf.py "app:create_app()"
