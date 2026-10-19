# Exam Answer Checker

Checks a candidate's answers against the official answer key when the questions are in a different order in
the two documents.

Upload two PDFs:

1. **Answer key**: the scanned question paper, with the correct option of every question marked by a hand-drawn tick.
2. **Response sheet**: the candidate's digital response sheet from the exam portal (TNPSC / TCS iON format), with the
   same questions in a shuffled order and the option the candidate chose.

The service matches every response question to its key question by the question **text**, reads the ticked option
from the scan, compares it with the candidate's choice and produces a score. You review anything uncertain before
seeing the results.

## Documentation

Full documentation is in [`docs/`](docs/README.md): a [user guide](docs/USER_GUIDE.md),
[operations](docs/OPERATIONS.md), [architecture](docs/ARCHITECTURE.md), the [processing pipeline](docs/PIPELINE.md),
[tick detection and tuning](docs/TICK_DETECTION.md), [HTTP routes and output formats](docs/API.md),
[development](docs/DEVELOPMENT.md) and [troubleshooting](docs/TROUBLESHOOTING.md). This README is the short version.

## Running it

```sh
cp .env.example .env      # optional: edit settings
docker compose up --build
```

Open <http://localhost:8000>. A 200-question, ~120-page scanned key takes a few minutes to OCR (about 4 minutes on
2 CPU cores).

Without Compose:

```sh
docker build -t exam-answer-checker .
docker run -p 8000:8000 -v exam-data:/data exam-answer-checker
```

Running locally without Docker needs Python 3.11+, `tesseract` (5.x, with `eng`) and `poppler-utils` (`pdftoppm`):

```sh
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload
```

## How it works

| Stage | Module | What it does |
|---|---|---|
| Validate | `validate.py` | Real PDF (`%PDF` header), not encrypted, size and page limits, response-sheet format recognised, `Q.n` numbers complete with no duplicates, equal question counts in both files. |
| Parse response sheet | `response_parser.py` | Reads `Q.n`, Question ID, Status and Chosen Option from the **text layer** (never OCR). Crops each embedded question image at 200 dpi and OCRs it (`--psm 6`). |
| Answer key OCR | `key_ocr.py` | Renders pages with `pdftoppm` and OCRs them (`--psm 4`, keeping word boxes). Splits the text stream after every “Answer not known” line, since each question ends with option (E). Margin question numbers are read in a separate pass and used only as a cross-check. |
| Match | `matching.py` | Normalises the text (drops vowel-less tokens, which removes most Tamil OCR noise). Builds character 3–5-gram TF-IDF cosine similarity, then makes a one-to-one assignment with the Hungarian algorithm. A pair is flagged when score < 0.35, its margin over the runner-up is < 0.08, or the row's best match disagrees with the assignment. |
| Ticks | `ticks.py` | Locates the (A)–(E) labels, anchored on the (E) line. A ticked label often OCRs as junk or vanishes, so missing positions are inferred from the column or 2×2 grid layout. Three signals around each label decide the tick: a stroke larger than any printed glyph, coloured pen ink, and excess dark ink. One clear winner is accepted. Otherwise the question is **uncertain**. |
| Option order | `options.py` | Where both crops' option texts are readable, it compares them letter by letter. A confident permutation translates the key's letter to the response's letter. A doubtful one is only flagged. |
| Score | `scoring.py` | Marks per correct answer, negative marks, and how to treat E (“Answer not known”) and unanswered questions. |
| Outputs | `outputs.py` | `evaluation.csv`, `results.json` and the reordered response-sheet PDF. |
| Jobs and web | `jobs.py`, `main.py` | Runs processing in a background thread, keeps job status as JSON on disk, serves the review and results pages and deletes old jobs. |

Format-specific parsing sits behind small interfaces (`ResponseSheetFormat` in `response_parser.py`, plus
`key_ocr.segment`), so other layouts can be added later.

### Review screen

When anything is flagged, a review page opens before the results. It shows the key crop (with its tick) beside the
response crop. You can:

- confirm a match, or pick another response question (type to search, or click a suggested alternative). Changing a
  match swaps the two rows, so the mapping stays one-to-one.
- confirm or correct the key answer.
- tick **Show all pairs** to go through every question, not just the flagged ones.

You can return to the review from the results page at any time.

### Outputs

- **Results page**: correct, wrong, chose E, unanswered, marks, the cut-off count (number of correct answers) and a
  colour-coded table. You can change the scoring settings and recalculate there.
- **evaluation.csv**: `Answer key Q, Response sheet Q, Question ID, Key answer, Chosen option, Result, Match score, Flagged`.
- **reordered_response.pdf**: a summary page, then every response-sheet block in answer-key order with its original
  `Q.n`. A green or red bar above each block reads `Answer key Q<k> = Response Q.<n>  Key: X  Chosen: Y  CORRECT/WRONG`.
  Blocks stay vector: each is the source page clipped to the block and merged onto A4, and text outside the block is
  removed from the content stream, so copying or searching finds each question exactly once.
- **results.json**: everything, including the mapping, match scores, tick measurements, flags and the scoring settings.

## Configuration

All settings are environment variables. See `.env.example` for the full list with comments.

| Variable | Default | |
|---|---|---|
| `RETENTION_HOURS` | 24 | Jobs are deleted this long after upload. There is also a **Delete now** button. |
| `MAX_FILE_MB` / `MAX_PAGES` | 50 / 300 | Per-file upload limits. |
| `RENDER_DPI` | 200 | Render resolution for the key and the crops. |
| `OCR_THREADS` | CPU count | Parallel Tesseract processes. |
| `JOB_WORKERS` | 1 | Jobs processed at once. |
| `MATCH_MIN_SCORE` / `MATCH_MIN_MARGIN` | 0.35 / 0.08 | Thresholds for flagging a match for review. |
| `TICK_RATIO` / `TICK_RUNNER_UP_RATIO` | 1.6 / 1.3 | How clearly the dark-ink signal must single out one label. |
| `MARKS_CORRECT` / `MARKS_WRONG` | 1.5 / 0 | Defaults on the upload form. |
| `AI_FALLBACK`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | off, unset, `claude-sonnet-5-5` | Optional Claude fallback for unclear ticks. |

### AI fallback (optional, off by default)

With `AI_FALLBACK=1` and `ANTHROPIC_API_KEY` set, the upload form shows a checkbox. When it is ticked, every question
whose tick the image analysis could not decide is sent to Claude as an image of that **answer-key** question, with a
strict one-letter prompt. Response-sheet content is never sent. The UI states when images are sent to the external
API, and the answers it supplied are labelled “AI”.

## Privacy

- Uploads, crops and outputs live in a per-job folder (`DATA_DIR/jobs/<random id>/`). A background task deletes jobs
  after `RETENTION_HOURS`, and **Delete now** removes one immediately.
- Logs contain only job IDs and stage names: no names, registration numbers, file contents or exception messages.
- The job ID is an unguessable random token and acts as the only access key, since there are no user accounts. Run
  the service behind your own authentication if it is reachable by others.

## Tests

```sh
docker compose run --rm test                     # or:
docker build --build-arg DEV=1 -t exam-answer-checker-dev .
docker run --rm -v "$PWD":/app -w /app -e DATA_DIR=/tmp/data exam-answer-checker-dev python -m pytest -q
```

`tests/synth.py` generates realistic synthetic fixtures. The response sheet has a text layer and embedded question
JPEGs. The key is an image-only scan with a pink background, skew, show-through, footers, column and 2×2 option
layouts, and pen ticks, some of them faint. The unit and robustness tests use these fixtures: parser, validation,
matching under 5–15% character noise, tick detection, option order, scoring, the PDF builder, review swaps and the
full web flow.

### Golden test with the real sample files

Copy the real files into `tests/fixtures/`:

- `answer_key.pdf`: TNPSC CTSE Paper II (code 504) scanned key, 118 pages, 200 questions
- `response_sheet.pdf`: the matching iON response sheet, 119 pages
- `evaluation.csv`: the hand-verified result, with columns that include `Answer key Q` and `Response sheet Q`

Then run:

```sh
docker run --rm -v "$PWD":/app -w /app -e DATA_DIR=/tmp/data exam-answer-checker-dev \
    python -m pytest tests/test_golden.py -s
```

The test checks 200 questions in each file, an exact match to the hand-made mapping, and tick accuracy against the
known key string (≥ 97% among confident detections, with every error caught as uncertain). It also checks the final
score: 119 correct, 81 wrong, 0 unanswered, 178.5 marks. The `-s` flag prints the tick report. To measure tick
accuracy with the AI fallback, add `-e GOLDEN_AI=1 -e ANTHROPIC_API_KEY=...`.

To see where the tick detector places each option label on a key, and why it chose what it did, run
`python -m tools.debug_ticks KEY.pdf OUT_DIR [--golden]`. See [docs/TICK_DETECTION.md](docs/TICK_DETECTION.md).

## Known limitations

- Only the TNPSC-style scanned key and the iON digital response sheet are supported.
- The tick detector was developed against synthetic scans. Label-box sizes and thresholds (`BOX_*`, `FAINT_DELTA` in
  `ticks.py`, `TICK_*` settings) may need tuning on real keys. The review screen catches whatever stays uncertain.
- Question 1 is assumed to start on the first page that has an “Answer not known” line, unless the margin number “1”
  is read on an earlier page. A key whose first question spans the cover's following page may need checking.
- If a scan loses an “Answer not known” line completely (for example, it is cut off), the question counts will not
  match and the upload is rejected with both counts shown.
- Maths-heavy questions OCR poorly. They are matched on whatever text survives, so expect more of them on the review
  screen.
- Jobs run in-process. Restarting the server marks running jobs as interrupted, and they must be uploaded again.
