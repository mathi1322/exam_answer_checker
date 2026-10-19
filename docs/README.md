# Documentation

| Document | For | Covers |
|---|---|---|
| [USER_GUIDE.md](USER_GUIDE.md) | People checking their exam | Uploading, the review screen and its flags, results, downloads, deleting data, accuracy |
| [OPERATIONS.md](OPERATIONS.md) | Whoever hosts the service | Deployment, every setting, sizing, AI fallback, privacy measures, monitoring, upgrades |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Developers | Components, job lifecycle, job folder, `job.json` / `state.json`, data model, concurrency |
| [PIPELINE.md](PIPELINE.md) | Developers | Every processing stage in detail: validation, parsing, OCR, segmentation, matching, option order, scoring, the PDF builder |
| [TICK_DETECTION.md](TICK_DETECTION.md) | Developers tuning accuracy | How ticks are found and decided, the AI fallback, and a step-by-step tuning procedure with the debug tool |
| [API.md](API.md) | Integrators | HTTP routes and form fields, a curl workflow, `evaluation.csv` / `results.json` / PDF formats |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Contributors | Dev setup, code conventions, the test suite, synthetic and golden fixtures, adding formats and features |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Everyone | Error messages and wrong-looking results, with causes and fixes |
| [FAQ.md](FAQ.md) | Everyone | Does it use paid AI, how the OCR works, whether Tesseract is good enough, why there is no database |
| [ROADMAP.md](ROADMAP.md) | Maintainers | Planned improvements in priority order, and when to add a database |
| [REVIEW-2026-10.md](REVIEW-2026-10.md) | Maintainers | The October 2026 code review: scores, findings, what was fixed and what is open |

New to the project? Start with FAQ. New to the code? Read ARCHITECTURE, then PIPELINE, then TICK_DETECTION.
