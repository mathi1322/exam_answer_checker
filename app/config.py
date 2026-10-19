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
    retention_hours: float = field(default_factory=lambda: _env("RETENTION_HOURS", 24.0, float))
    render_dpi: int = field(default_factory=lambda: _env("RENDER_DPI", 200, int))
    ocr_threads: int = field(default_factory=lambda: _env("OCR_THREADS", os.cpu_count() or 2, int))
    job_workers: int = field(default_factory=lambda: _env("JOB_WORKERS", 1, int))

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
