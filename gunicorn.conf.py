"""Gunicorn settings, picked up automatically from the working directory.

The app's live updates poll the server (bell/badges every 20s, an open
message thread every 5s), so a single sync worker would queue those small
requests behind slow ones. Several workers, each with a few threads, keep
polls fast; threads suit this workload because most time is spent waiting
on Postgres.
"""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
# Capped at 5 by default: each thread can hold a Postgres connection
# (CONN_MAX_AGE), and 5 x 4 = 20 fits small managed-database plans. Raise
# GUNICORN_WORKERS on bigger servers once the database allows it.
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 5)))
threads = int(os.environ.get("GUNICORN_THREADS", 4))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))  # file uploads (drawings up to 100 MB) need headroom
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically so a slow memory leak can't build up.
max_requests = 2000
max_requests_jitter = 200

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
