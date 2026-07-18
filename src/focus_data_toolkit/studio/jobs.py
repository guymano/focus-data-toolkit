"""Background conversion jobs + managed sources for the Studio.

By default a single worker runs one conversion at a time (extra submissions queue), so two large
conversions cannot exhaust the machine. Each job/source gets its own directory under the work
root; a TTL sweep (and a startup sweep) removes old ones and recovers any interrupted atomic
publishes. Conversions run the Core streaming engine with the progress/cancel hooks, so the
Studio's outputs are identical to a CLI run and cancellation never leaves partial output.
"""

from __future__ import annotations

import queue
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from focus_data_toolkit.io.atomic_writer import clean_leftovers
from focus_data_toolkit.studio.security import resolve_within_root


@dataclass
class Job:
    """State of one conversion job (mutated by the worker thread; read by the API)."""

    id: str
    out_dir: Path
    status: str = "queued"  # queued | running | succeeded | failed | cancelled
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    created: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)

    @property
    def done(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")

    def summary(self) -> dict:
        last = self.events[-1] if self.events else None
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "error_code": self.error_code,
            "last_event": last,
            "event_count": len(self.events),
        }


class JobManager:
    """Owns the work root, a bounded worker pool, and the job/source registries."""

    def __init__(self, work_dir: Path, *, max_concurrency: int = 1, ttl_seconds: int = 86400):
        self._work = Path(work_dir)
        self._jobs_dir = self._work / "jobs"
        self._sources_dir = self._work / "sources"
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        self._sources_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._workers = [
            threading.Thread(target=self._worker, name=f"fdt-studio-{i}", daemon=True)
            for i in range(max(1, max_concurrency))
        ]
        for worker in self._workers:
            worker.start()
        self._sweep_disk_startup()

    # --- managed sources (uploads / generated files) ------------------------------------
    def new_source_dir(self) -> tuple[str, Path]:
        source_id = uuid.uuid4().hex
        path = self._sources_dir / source_id
        path.mkdir(parents=True, exist_ok=True)
        return source_id, path

    def source_file(self, source_id: str, name: str) -> Path:
        base = resolve_within_root(source_id, self._sources_dir)
        return resolve_within_root(name, base)

    # --- jobs ---------------------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def job_file(self, job_id: str, name: str) -> Path:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return resolve_within_root(name, job.out_dir)

    def submit_convert(self, run: Callable[[Job], None]) -> Job:
        """Register a job and enqueue ``run(job)`` (executed on a worker thread)."""
        self._sweep_ttl()
        job_id = uuid.uuid4().hex
        job_root = self._jobs_dir / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, out_dir=job_root / "result")
        with self._lock:
            self._jobs[job_id] = job
        self._queue.put((job, run))
        return job

    def _worker(self) -> None:
        while True:
            job, run = self._queue.get()
            try:
                if job.cancel.is_set():
                    job.status = "cancelled"
                else:
                    job.status = "running"
                    run(job)
            except Exception as exc:  # backstop: a job callable must never kill the worker
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished.set()
                self._queue.task_done()

    # --- cleanup ------------------------------------------------------------------------
    def _sweep_ttl(self) -> None:
        cutoff = time.monotonic() - self._ttl
        stale = [j for j in list(self._jobs.values()) if j.done and j.created < cutoff]
        for job in stale:
            with self._lock:
                self._jobs.pop(job.id, None)
            shutil.rmtree(self._jobs_dir / job.id, ignore_errors=True)

    def _sweep_disk_startup(self) -> None:
        # Recover any interrupted atomic publish and drop leftover staging under the work root.
        try:
            clean_leftovers(self._work)
        except OSError:
            pass  # best-effort startup hygiene
