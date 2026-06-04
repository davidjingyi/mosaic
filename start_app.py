"""
Onco-RAG startup: gunicorn with uvicorn workers (pre-fork model).
Master loads engine once, workers share memory via CoW.
"""
import os
import logging

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "none")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("startup")

worker_count = os.environ.get("ONCO_WORKERS", "2")
logger.info(f"Starting Onco-RAG v2.0 with gunicorn ({worker_count} workers, preload)")

import subprocess
import sys

cmd = [
    sys.executable, "-m", "gunicorn",
    "app.main:app",
    "-c", os.path.join(os.path.dirname(__file__), "gunicorn_conf.py"),
]
os.execv(sys.executable, cmd)
