"""Runtime settings, read from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return cast(raw)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    max_file_mb: float = field(default_factory=lambda: _env("MAX_FILE_MB", 50.0, float))
    max_pages: int = field(default_factory=lambda: _env("MAX_PAGES", 300, int))
    max_page_inches: float = field(default_factory=lambda: _env("MAX_PAGE_INCHES", 20.0, float))
    retention_hours: float = field(default_factory=lambda: _env("RETENTION_HOURS", 2.0, float))
    render_dpi: int = field(default_factory=lambda: _env("RENDER_DPI", 200, int))
    ocr_threads: int = field(default_factory=lambda: _env("OCR_THREADS", os.cpu_count() or 2, int))
    job_workers: int = field(default_factory=lambda: _env("JOB_WORKERS", 1, int))

    # Public-deployment limits. Uploads are refused (not queued) when any of them is reached.
    uploads_per_hour: int = field(default_factory=lambda: _env("UPLOADS_PER_HOUR", 10, int))  # per client IP
    max_pending_jobs: int = field(default_factory=lambda: _env("MAX_PENDING_JOBS", 10, int))  # queued + processing
    max_disk_gb: float = field(default_factory=lambda: _env("MAX_DISK_GB", 5.0, float))  # all job folders together

    # Matching review thresholds
    match_min_score: float = field(default_factory=lambda: _env("MATCH_MIN_SCORE", 0.35, float))
    match_min_margin: float = field(default_factory=lambda: _env("MATCH_MIN_MARGIN", 0.08, float))

    # Tick detection: winner ink must be >= ratio x median ink of the other labels,
    # and >= runner_up_ratio x the second-highest label.
    tick_ratio: float = field(default_factory=lambda: _env("TICK_RATIO", 1.6, float))
    tick_runner_up_ratio: float = field(default_factory=lambda: _env("TICK_RUNNER_UP_RATIO", 1.3, float))

    # Optional Claude fallback for uncertain ticks (off unless enabled AND a key is set)
    ai_fallback: bool = field(default_factory=lambda: _env("AI_FALLBACK", False, bool))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))

    # Scoring defaults (overridable per job in the upload form)
    marks_correct: float = field(default_factory=lambda: _env("MARKS_CORRECT", 1.5, float))
    marks_wrong: float = field(default_factory=lambda: _env("MARKS_WRONG", 0.0, float))

    @property
    def ai_available(self) -> bool:
        return self.ai_fallback and bool(os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


settings = Settings()
