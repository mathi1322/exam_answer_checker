# Operations guide

This guide covers deploying, configuring and running the service. It is written for whoever hosts it.

## Requirements

- **Docker** (recommended). The image includes Python 3.12, Tesseract 5 with English, and Poppler.
- Without Docker: Python 3.11+, `tesseract` 5.x with the `eng` language, and `pdftoppm` (`poppler-utils`).
- **CPU:** OCR is the bottleneck. A 118-page key takes about 4 minutes on 2 cores. More cores (with `OCR_THREADS`)
  shorten it almost linearly.
- **Memory:** about 0.5 GB per concurrent job is comfortable.
- **Disk** (estimates, not measured on the real sample): a few hundred MB per job at peak while the page renders
  exist. The renders are deleted when processing ends, leaving the uploads, about 400 question crops and the outputs
  (tens of MB) until expiry.

## Deploying

### Docker Compose

```sh
cp .env.example .env        # edit as needed
docker compose up -d --build
docker compose logs -f web
```

The `web` service listens on port 8000 and keeps all job data in the named volume `exam-data` (mounted at `/data`).
The compose file passes settings from `.env` through `${VAR:-default}` substitution, so `.env` is optional.

### Plain Docker

```sh
docker build -t exam-answer-checker .
docker run -d --name exam-checker -p 8000:8000 -v exam-data:/data --env-file .env exam-answer-checker
```

Leave out `--env-file .env` if you have no `.env` file. Docker refuses to start when the file is missing.

### Without Docker

```sh
pip install -r requirements.txt
export DATA_DIR=/var/lib/exam-checker
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Run **one process**, without `--workers N`. Jobs run in threads inside the process, and the job store assumes a
single process owns it: a second process would mark the first one's running jobs as interrupted at startup.

### Behind a reverse proxy

Put it behind nginx, Caddy or similar for TLS and access control. Allow uploads of at least 2 × `MAX_FILE_MB` per
request (both files arrive in one request), for example `client_max_body_size 110m;` in nginx. Request timeouts can
stay short. Uploads return as soon as the files are saved, and the slowest synchronous request (review submission,
which rebuilds the PDF) takes a few seconds.

## Configuration

All settings are environment variables, read once at startup. `.env.example` has the full list with comments.

| Variable | Default | Description |
|---|---|---|
| `DATA_DIR` | `./data` (`/data` in Docker) | Root of the job folders |
| `RETENTION_HOURS` | `24` | Age, in hours from upload, at which a job folder is deleted |
| `MAX_FILE_MB` | `50` | Per-file upload limit |
| `MAX_PAGES` | `300` | Per-file page limit |
| `RENDER_DPI` | `200` | Render resolution for the key and the crops. 300 can help with very small print, but OCR takes about twice as long |
| `OCR_THREADS` | CPU count | Parallel Tesseract/pdftoppm processes per job |
| `JOB_WORKERS` | `1` | Jobs processed at the same time. Others wait in `queued` |
| `MATCH_MIN_SCORE` | `0.35` | Flag a match below this text similarity |
| `MATCH_MIN_MARGIN` | `0.08` | Flag a match whose lead over the runner-up is smaller than this |
| `TICK_RATIO` | `1.6` | Dark-ink signal: ticked label vs the median of the others |
| `TICK_RUNNER_UP_RATIO` | `1.3` | Dark-ink signal: ticked label vs the second highest |
| `MARKS_CORRECT` | `1.5` | Default shown on the upload form |
| `MARKS_WRONG` | `0` | Default shown on the upload form |
| `AI_FALLBACK` | `0` | `1` enables the optional Claude fallback (it also needs a key) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Model used for the fallback |

The matching and tick thresholds trade review workload against the risk of a silent error. Lowering a threshold flags
fewer questions, but lets more doubtful ones through unreviewed. See [TICK_DETECTION.md](TICK_DETECTION.md#tuning-on-a-real-key)
before changing the tick settings.

### Sizing `OCR_THREADS` and `JOB_WORKERS`

Each running job uses up to `OCR_THREADS` processes, so peak CPU is about `JOB_WORKERS × OCR_THREADS`. On a small
server, keep `JOB_WORKERS=1` and set `OCR_THREADS` to the core count. Queued jobs wait their turn, and their progress
page says "Waiting to start".

## AI fallback

When `AI_FALLBACK=1` and `ANTHROPIC_API_KEY` are set, the upload form shows a checkbox. For jobs where it is ticked,
each answer-key question whose tick could not be read is sent once to the Anthropic Messages API as a JPEG, and the
model is asked for a single letter.

- **What is sent:** only images of answer-key questions, which come from a public document. Response-sheet content
  is never sent.
- **Cost and volume:** one small request per uncertain question (often a handful, at most one per question), each
  with one image and `max_tokens=10`.
- **Review:** AI answers are used for scoring but flagged on the review page, so a person confirms each one.
- **Failures:** an API error is logged (exception type only) and the question simply stays uncertain for review. The
  job does not fail.
- **Disclosure:** the upload form, review page and results page all say when images were sent to the external API,
  and answers from the AI are labelled.

## Privacy and data handling

Response sheets contain personal data: name, registration and application numbers.

| Measure | How |
|---|---|
| **Storage is per job** | Everything for a job lives in `DATA_DIR/jobs/<id>/` and nowhere else. No database, no shared cache |
| **Automatic deletion** | A background thread deletes job folders `RETENTION_HOURS` after upload (checked every 10 minutes and at startup) |
| **Manual deletion** | "Delete now" on the results page removes the folder immediately |
| **Logs** | Only job IDs, stage events and exception *types* with stack frames. Never names, IDs, file contents or exception messages |
| **Outputs** | The reordered PDF's summary page shows the subject, not the candidate's name. The response blocks themselves are copied unchanged |
| **Access** | Job URLs contain a 128-bit random token. There are no accounts. Put the service behind authentication if strangers can reach it |
| **Uploads** | Validated before processing. The PDFs are parsed with pdfplumber and pypdf and rendered by Poppler, and never executed |

Backups: job data is deliberately short-lived. Do **not** back up the `/data` volume. There is nothing in it worth
keeping beyond the retention window.

## Monitoring

- **Health:** `GET /healthz` returns 200 `{"ok": true}` when Tesseract and pdftoppm are available. The image's
  `HEALTHCHECK` and the compose file both use it.
- **Logs** (stdout), one line per event:
  ```
  INFO job <id> started
  INFO job <id> finished processing
  INFO job <id> rejected by validation
  ERROR job <id> failed with <ExceptionType>: <stack frames>
  INFO cleanup removed N expired job(s)
  ```
- **Disk:** the data volume should stay bounded by about (jobs per retention period × the size of one finished job).
  If it keeps growing, check for `cleanup removed N expired job(s)` log lines. They appear only when something
  expired, so a growing volume with none of them points to a stuck cleanup.

## Upgrades and restarts

- Rebuild and restart the container. Jobs in `review` or `done` survive restarts, because their state is on disk.
- Jobs that were `queued` or `processing` are marked `error` at startup with "interrupted by a server restart". Their
  users must upload again. To avoid this, restart when the status of every job is `review`, `done`, `invalid` or
  `error`.
- After an upgrade that changes the pipeline, older jobs keep the results computed by the old version until they
  are re-reviewed or rescored. Re-scoring then reuses their stored `state.json`.
- The image runs as the non-root user `app` (uid 10001). A volume created by an image from before October 2026 holds
  root-owned files that this user cannot write. Remove the volume, or change the owner once:
  `docker run --rm -u root -v exam-data:/data exam-answer-checker chown -R app:app /data`.
