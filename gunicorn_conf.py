"""
Onco-RAG gunicorn config: pre-fork model for CoW memory sharing.
"""
import os

# Worker count
workers = int(os.environ.get("ONCO_WORKERS", "2"))

# Bind
bind = "127.0.0.1:8001"

# Worker class
worker_class = "uvicorn.workers.UvicornWorker"

# Preload app in master before forking workers
# This ensures MedCT, BM25, etc. are loaded once and shared via CoW
preload_app = True

# Logging
loglevel = "warning"
accesslog = "-"
errorlog = "-"

# Timeouts
timeout = 0  # Disabled: workers handle long-running background tasks (embedding)
graceful_timeout = 30
