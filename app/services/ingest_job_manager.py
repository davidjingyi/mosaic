"""Ingest job manager for tracking background ingestion tasks."""
import asyncio
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

MAX_JOBS = 50


class IngestJobManager:
    """Lightweight job tracker stored in JSON. No Redis/Celery required."""

    def __init__(self, path: str = "data/ingest_jobs.json"):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                jobs = {k: v for k, v in raw.items()}
                # Mark any 'running' jobs from previous session as failed
                for job in jobs.values():
                    if job.get("status") == "running":
                        job["status"] = "failed"
                        if "errors" not in job:
                            job["errors"] = []
                        job["errors"].append("Server restarted while job was running")
                with self._lock:
                    self._jobs = jobs
                self._save()
            except Exception as e:
                logger.warning("Failed to load ingest jobs: %s", e)
                with self._lock:
                    self._jobs = {}

    def _save(self) -> None:
        try:
            with self._lock:
                jobs_snapshot = dict(self._jobs)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(jobs_snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Failed to save ingest jobs: %s", e)

    def _sync_from_disk(self) -> None:
        """Merge disk state with memory for multi-worker support.

        - New jobs from disk are added to memory.
        - Jobs in memory but not on disk are kept (worker-local state).
        - Fields from disk overwrite memory (progress updates from other workers).
        - Does NOT change status to failed (unlike _load).
        """
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                disk_jobs = json.load(f)
            with self._lock:
                for job_id, disk_job in disk_jobs.items():
                    if job_id in self._jobs:
                        # Update existing job with disk data (other worker's progress)
                        self._jobs[job_id].update(disk_job)
                    else:
                        # New job from another worker
                        self._jobs[job_id] = disk_job
        except Exception as e:
            logger.warning("Failed to sync ingest jobs from disk: %s", e)

    def _trim(self) -> None:
        """Keep only the most recent MAX_JOBS."""
        if len(self._jobs) <= MAX_JOBS:
            return
        sorted_ids = sorted(self._jobs, key=lambda k: self._jobs[k].get("created_at", 0))
        for old_id in sorted_ids[: len(self._jobs) - MAX_JOBS]:
            del self._jobs[old_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(self, doc_ids: list[str], job_type: str = "embed") -> str:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "job_type": job_type,
                "status": "pending",
                "total_docs": len(doc_ids),
                "current_doc": 0,
                "current_stage": "pending",
                "current_file": "",
                "progress_pct": 0,
                "errors": [],
                "doc_ids": doc_ids,
                "created_at": now,
                "updated_at": now,
            }
            self._trim()
        self._save()
        return job_id

    def update_job(
        self,
        job_id: str,
        status: str | None = None,
        current_doc: int | None = None,
        current_stage: str | None = None,
        current_file: str | None = None,
        progress_pct: int | None = None,
        error: str | None = None,
        chunk_current: int | None = None,
        chunk_total: int | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if status is not None:
                job["status"] = status
            if current_doc is not None:
                job["current_doc"] = current_doc
            if current_stage is not None:
                job["current_stage"] = current_stage
            if current_file is not None:
                job["current_file"] = current_file
            if progress_pct is not None:
                job["progress_pct"] = progress_pct
            if error is not None:
                if "errors" not in job:
                    job["errors"] = []
                job["errors"].append(error)
            if chunk_current is not None:
                job["chunk_current"] = chunk_current
            if chunk_total is not None:
                job["chunk_total"] = chunk_total
            job["updated_at"] = time.time()
        self._save()

    def get_job(self, job_id: str) -> dict | None:
        self._sync_from_disk()
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20, job_type: str | None = None) -> list[dict]:
        # Re-read from disk to support multi-worker environments (gunicorn pre-fork)
        self._sync_from_disk()
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.get("created_at", 0), reverse=True)
            if job_type:
                jobs = [j for j in jobs if j.get("job_type") == job_type]
            return jobs[:limit]

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") not in {"pending", "running"}:
                return False
            self._cancelled.add(job_id)
            job["status"] = "cancelled"
            job["updated_at"] = time.time()
        self._save()
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def make_progress_callback(self, job_id: str) -> Callable:
        """Return a callback suitable for KnowledgeBaseScanner.ingest_documents_sync.

        Raises RuntimeError('cancelled') when the job has been cancelled.
        Supports both document-level and chunk-level progress.
        """

        def callback(stage: str, current: int, total: int, file_name: str, chunk_current: int = None, chunk_total: int = None) -> None:
            if self.is_cancelled(job_id):
                raise RuntimeError("cancelled")
            pct = int((current / total) * 100) if total > 0 else 0
            self.update_job(
                job_id,
                status="running",
                current_doc=current,
                current_stage=stage,
                current_file=file_name,
                progress_pct=pct,
                chunk_current=chunk_current,
                chunk_total=chunk_total,
            )

        return callback
