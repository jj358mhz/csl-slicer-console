"""Gunicorn config for csl-slicer-console."""

import multiprocessing
import os

bind = "0.0.0.0:5000"
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"
timeout = 30
graceful_timeout = 30
keepalive = 5

# gunicorn 26+ opens a control socket in the working dir, which the app
# user can't write to. We don't use gunicornc, so turn it off.
control_socket_disable = True

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")

# Only forwarded proto/for from local/private ranges (behind Traefik/nginx)
forwarded_allow_ips = "*"
