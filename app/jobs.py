"""In-process background jobs with status kept as JSON files on disk.

Each job lives in DATA_DIR/jobs/<id>/ and is deleted after RETENTION_HOURS.
A job belongs to the browser that uploaded it: job.json keeps a hash of that browser's owner cookie.
Logs carry job ids and stage names only, never names, IDs or file contents.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import pipeline
from .config import Settings
from .scoring import ScoringConfig
from .validate import ValidationError

log = logging.getLogger("exam_checker")

QUEUED, PROCESSING, REVIEW, DONE, INVALID, ERROR = "queued", "processing", "review", "done", "invalid", "error"
_JOB_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class JobStore:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.root = cfg.jobs_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, cfg.job_workers), thread_name_prefix="job")

    # ---------------------------------------------------------------- storage

    def new(self, options: dict, owner: str = "") -> str:
        job_id = secrets.token_urlsafe(16)
        d = self.root / job_id
        d.mkdir(parents=True)
        self._write(job_id, {"id": job_id, "created": time.time(), "status": QUEUED, "stage": "Waiting to start",
                             "progress": 0.0, "message": "", "options": options, "owner": _owner_hash(owner)})
        return job_id

    @staticmethod
    def owned_by(job: dict, owner: str) -> bool:
        return bool(owner) and hmac.compare_digest(job.get("owner", ""), _owner_hash(owner))

    def dir(self, job_id: str) -> Optional[Path]:
        if not job_id or not set(job_id) <= _JOB_ID_CHARS:
            return None
        d = self.root / job_id
        return d if (d / "job.json").exists() else None

    def get(self, job_id: str) -> Optional[dict]:
        d = self.dir(job_id)
        if d is None:
            return None
        try:
            return json.loads((d / "job.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                return
            job.update(fields)
            self._write(job_id, job)

    def _write(self, job_id: str, job: dict) -> None:
        d = self.root / job_id
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job))
        tmp.replace(d / "job.json")

    def delete(self, job_id: str) -> bool:
        d = self.dir(job_id)
        if d is None:
            return False
        shutil.rmtree(d, ignore_errors=True)
        log.info("job %s deleted", job_id)
        return True

    def pending_count(self) -> int:
        return sum(1 for d in self.root.iterdir() if (job := self.get(d.name)) and job["status"] in (QUEUED, PROCESSING))

    def disk_usage(self) -> int:
        """Bytes used by all job folders."""
        total = 0
        for f in self.root.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:  # deleted by cleanup while we were walking
                pass
        return total

    # ---------------------------------------------------------------- running

    def submit(self, job_id: str) -> None:
        self._executor.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        d = self.dir(job_id)
        job = self.get(job_id)
        if d is None or job is None:
            return
        opts = job["options"]
        self.update(job_id, status=PROCESSING, started=time.time())
        log.info("job %s started", job_id)

        def progress(stage: str, frac: float) -> None:
            self.update(job_id, stage=stage, progress=round(frac, 3))

        try:
            state = pipeline.process(d, self.cfg, use_ai=bool(opts.get("use_ai")), progress=progress)
            if pipeline.needs_review(state):
                self.update(job_id, status=REVIEW, stage="Waiting for your review", progress=1.0,
                            finished=time.time())
            else:
                pipeline.finalize(d, state, ScoringConfig(**opts["scoring"]))
                self.update(job_id, status=DONE, stage="Finished", progress=1.0, finished=time.time())
            log.info("job %s finished processing", job_id)
        except ValidationError as exc:
            self.update(job_id, status=INVALID, message=exc.message, finished=time.time())
            _remove_uploads(d, "response.pdf")
            log.info("job %s rejected by validation", job_id)
        except Exception as exc:
            self.update(job_id, status=ERROR, finished=time.time(),
                        message="Something went wrong while processing the files. Please try again.")
            _remove_uploads(d, "response.pdf")
            # Stack frames and exception type only: messages could quote file contents.
            log.error("job %s failed with %s:\n%s", job_id, type(exc).__name__,
                      "".join(traceback.format_tb(exc.__traceback__, limit=-8)))
        finally:
            # Only the crops are needed from the key after processing. The response sheet stays while the
            # job exists, because review and rescoring rebuild the reordered PDF from it.
            _remove_uploads(d, "key.pdf")

    def mark_interrupted(self) -> None:
        """Jobs left running by a previous process will never finish."""
        for d in self.root.iterdir():
            job = self.get(d.name)
            if job and job["status"] in (QUEUED, PROCESSING):
                self.update(d.name, status=ERROR, message="Processing was interrupted by a server restart. "
                                                         "Please upload the files again.")
                _remove_uploads(d, "key.pdf", "response.pdf")

    # ---------------------------------------------------------------- cleanup

    def cleanup(self) -> int:
        cutoff = time.time() - self.cfg.retention_hours * 3600
        removed = 0
        for d in list(self.root.iterdir()):
            job = self.get(d.name)
            created = job["created"] if job else d.stat().st_mtime
            if created < cutoff and (not job or job["status"] not in (QUEUED, PROCESSING)):
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        if removed:
            log.info("cleanup removed %d expired job(s)", removed)
        return removed

    def start_cleanup_thread(self, interval_s: float = 600) -> None:
        def loop():
            while True:
                try:
                    self.cleanup()
                except Exception:
                    log.exception("cleanup failed")
                time.sleep(interval_s)

        threading.Thread(target=loop, daemon=True, name="cleanup").start()


def _owner_hash(owner: str) -> str:
    return hashlib.sha256(owner.encode()).hexdigest() if owner else ""


def _remove_uploads(d: Path, *names: str) -> None:
    for name in names:
        (d / name).unlink(missing_ok=True)
