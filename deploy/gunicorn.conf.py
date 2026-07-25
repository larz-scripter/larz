# gunicorn config for a Larz app.  Run: gunicorn -c deploy/gunicorn.conf.py app:app
import multiprocessing, os
bind = "0.0.0.0:%s" % os.environ.get("PORT", "8000")
workers = int(os.environ.get("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1))
threads = 4                      # Larz uses background threads (jobs/scheduler)
worker_class = "gthread"
timeout = 60
keepalive = 5
accesslog = "-"
errorlog = "-"
# NOTE: background jobs/scheduler run per-worker. For a single scheduler, run one
# dedicated worker (or a separate `larz worker` process) — see deploy/DEPLOY.md.
