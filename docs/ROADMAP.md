# Roadmap

Planned improvements, most important first. Which items are urgent depends on how the service is run:

- **One person, on their own machine:** items 1 and 5, and possibly 4. The rest can wait.
- **Shared with others or reachable from the internet:** items 2 and 3 were required before anyone else used it,
  and are done (October 2026). See [OPERATIONS.md](OPERATIONS.md#on-the-public-internet).

Items marked *(review)* come from the [October 2026 review](REVIEW-2026-10.md).

## 1. Measure accuracy on real papers

The golden test has never run in this repository: the real files are not committed, so it is skipped. Until it
runs, the true match and tick accuracy on real scans is unknown, and the tick thresholds were tuned on synthetic
scans.

- Put `answer_key.pdf`, `response_sheet.pdf` and the hand-checked `evaluation.csv` in `tests/fixtures/`
  (gitignored) and run `tests/test_golden.py`. See [DEVELOPMENT.md](DEVELOPMENT.md#golden-test).
- Repeat with two or three other papers if possible, with `python -m tools.debug_ticks KEY.pdf OUT --expected ...`
  for keys without a golden CSV.
- Use the results to decide whether OCR or the tick thresholds need work (see [FAQ.md](FAQ.md#is-tesseract-good-enough-or-should-we-use-something-else)).

## 2. Access control *(review)* — done

Anyone with a job URL could see that candidate's results. The service is meant to be open to the public, so instead
of a password:

- Done: jobs belong to the uploading browser (owner cookie, checked on every job route).
- Done: CSRF protection on the POST forms (same-origin check plus a `SameSite=Lax` cookie).
- Done: per-IP upload rate limit, and a cap on queued jobs.
- Later, if abuse appears: a CAPTCHA on the upload form, or accounts (which need a database, see below).

## 3. Protect the server from hostile uploads *(review)* — done

- Done: page dimensions are checked (`MAX_PAGE_INCHES`), not only the page count.
- Done: oversized requests are refused at Caddy (`MAX_REQUEST_MB`) and by the app from `Content-Length`, before
  Starlette spools the upload.
- Done: total disk use across jobs is capped (`MAX_DISK_GB`).

## 4. Survive restarts

A restart or deploy during processing marks the job `error`, and the user must upload again. The uploads are still
on disk, so the simple fix is to resubmit `queued` and `processing` jobs at startup instead of failing them. A real
job queue is only worth it together with a database (see below).

## 5. Continuous integration

Add a GitHub Actions workflow that builds the dev image and runs `pytest` on every push and pull request, plus
`pip-audit` on `requirements.txt`. It would have caught the pypdf deprecation fixed in the review automatically.

## 6. Smaller fixes *(review)*

| Item | Where | Notes |
|---|---|---|
| Set a timeout on the Claude API call | `ticks.ai_tick` | The SDK default is long, with retries, so a slow API can stall a job for minutes per question |
| Summary counts overlap | `scoring.score` | `chose_e` includes rows also counted as `key_unknown`, and `unanswered` overlaps `wrong` when `unanswered_policy="wrong"`. The stat tiles can add up to more than the total |
| Text inside Form XObjects stays searchable in the reordered PDF | `outputs._block_page` | Only top-level text operators are filtered. Not an issue for the current iON sheets |
| Tamil OCR | Dockerfile, `key_ocr`, `response_parser` | Install `tesseract-ocr-tam` if real-paper tests show matching would benefit |
| Pin the base image by digest | Dockerfile | Reproducible builds |

## 7. Later

- Other exam formats. Only the TNPSC scanned key and the iON response sheet are supported. See
  [DEVELOPMENT.md](DEVELOPMENT.md#adding-a-new-exam-format).
- Keeping results beyond the retention window, which needs accounts and a database.

## When to add a database

The current design (one folder per job, no database) is right while the service runs as one process for one person
or a small team. Add a database when any of these is needed:

| Need | Why files are not enough |
|---|---|
| User accounts and login | Results must belong to a user, not just a URL token |
| History of past results | Jobs are deleted after `RETENTION_HOURS` by design |
| More than one server or process | `JobStore` assumes a single process owns `DATA_DIR` |
| Jobs that survive restarts reliably | Processing runs in threads inside the web process |

The usual setup is Postgres for jobs and users, S3-compatible object storage for the PDFs and crops, and a queue
(Redis with RQ or Celery) running the pipeline in separate worker processes. The stage modules already take paths
and return data, so only `jobs.py` and `main.py` would change substantially.
