# Design FAQ

Short answers to the questions people ask first about how the checker works: whether it uses paid AI, how the OCR
works, whether Tesseract is good enough, and why there is no database. Each answer links to the detailed document.

## Does it use a paid AI model to read or match the PDFs?

No. Reading, matching, tick detection and scoring all run locally with free, open-source tools inside the Docker
image. With the default settings the service works offline and costs nothing beyond the machine it runs on.

| Stage | How it works | Tools |
|---|---|---|
| Response sheet fields (Q.n, Question ID, chosen option) | Read directly from the PDF's text layer, no OCR | pdfplumber |
| Question text | OCR of the question images (response sheet) and of the scanned key pages | Tesseract, pdftoppm |
| Matching | Character n-gram TF-IDF similarity, then a one-to-one assignment with the Hungarian algorithm | scikit-learn, SciPy |
| Tick detection | Pixel analysis around each option label (stroke size, pen colour, dark ink) | OpenCV, NumPy |
| Scoring and outputs | Counting and PDF assembly | pypdf, reportlab |

The only paid component is the **optional** Claude fallback for unclear ticks. It is off unless the host sets
`AI_FALLBACK=1` and an `ANTHROPIC_API_KEY`, **and** the user ticks the checkbox on the upload form. Even then:

- it is asked only about ticks the image analysis could not decide, never about matching;
- only the image of that answer-key question is sent, never anything from the response sheet;
- it costs one small request per unclear question, usually a handful per paper;
- its answers are flagged on the review page so a person confirms them.

See [OPERATIONS.md](OPERATIONS.md#ai-fallback) and [TICK_DETECTION.md](TICK_DETECTION.md#step-4-optional-ai-fallback-ai_tick).

## How does the OCR work?

OCR is done by **Tesseract 5**, installed in the image from the Debian packages `tesseract-ocr` and
`tesseract-ocr-eng`. The Python library `pytesseract` only runs the `tesseract` program on an image and reads its
output.

Tesseract 5 recognises text with a small neural network (an LSTM) shipped as the `eng.traineddata` file. It runs on
the CPU, needs no network or account and has no per-page cost. It is a machine-learning model in that narrow sense,
but not a hosted AI service.

| Document | What is OCR'd | Settings |
|---|---|---|
| Answer key (scan, no text layer) | Every page, rendered to JPEG by `pdftoppm` at `RENDER_DPI` | `--psm 4` (single column), word boxes kept. A second `--psm 6` pass reads only the left margin for question numbers |
| Response sheet (digital PDF) | Only the embedded question image of each block | `--psm 6` (one block of text) |

The word positions from the key are used to split it into questions, to locate the (A)–(E) labels for tick
detection and to crop each question for the review screen.

OCR is the slowest stage: about 4 minutes for a 118-page key on 2 cores. `OCR_THREADS` runs pages in parallel, and
the time falls almost linearly with more cores. Only English is installed, so Tamil text comes out as junk. Matching
removes that junk on purpose (tokens without vowels are dropped).

Details: [PIPELINE.md](PIPELINE.md), stages 2–4.

## Is Tesseract good enough, or should we use something else?

For this job, Tesseract is good enough. Switch only if measurements on real papers show OCR is the limit.

Why it fits:

- **Matching tolerates OCR errors.** It compares overlapping 3–5 character fragments, so a misread letter spoils
  only a few of them. The tests pass with 5–15% of characters deliberately corrupted.
- **The one-to-one assignment absorbs weak matches.** When 199 questions match confidently, the last one falls into
  the right place even if its text is poor.
- **OCR does not decide the tick.** It only helps locate the labels. Labels it misses are inferred from the layout,
  and the tick itself is decided from pixels.
- **Doubtful results go to review.** An OCR error becomes review work, not a silently wrong score.

Where it is weak on these papers: maths-heavy questions (symbols and fractions), Tamil (not installed), poor scans
(pink background, skew, show-through) and speed.

| Option | Cost | Accuracy on noisy scans | Trade-off |
|---|---|---|---|
| **Tesseract** (current) | Free, local | Good on clean print | Weak on maths and poor scans |
| PaddleOCR / docTR | Free, local | Noticeably better | 1–2 GB larger image, slower without a GPU |
| Google Vision / AWS Textract / Azure | Paid per page | Very good | Exam papers leave the server |
| Claude or another vision model | Paid per page | Can read the question and the tick together | Highest cost, external, less predictable |

What to do before considering a switch:

1. Run the golden test on real papers (see [DEVELOPMENT.md](DEVELOPMENT.md#golden-test)). It reports wrong matches
   and tick accuracy. If matching is 200/200 and only a few questions need review, nothing needs changing.
2. If results are borderline, try the cheap fixes first: `RENDER_DPI=300`, deskewing and binarising before OCR, and
   installing `tesseract-ocr-tam` so Tamil text also helps matching.
3. Only then replace the answer-key OCR with PaddleOCR. OCR is confined to `key_ocr.py` and `response_parser.py`, so
   the change is contained.

## Is there a database? How are the PDFs handled?

There is no database. Each upload is a **job folder** on disk:

```
DATA_DIR/jobs/<random id>/      (/data in Docker, kept in the "exam-data" volume)
├── job.json            status, progress, scoring settings
├── key.pdf             uploaded answer key
├── response.pdf        uploaded response sheet
├── pages/              key page renders, deleted as soon as processing ends
├── crops/              one image per question, for the review screen
├── state.json          OCR text, matches, ticks, review corrections
├── similarity.npy      the key × response match-score table
└── out/                evaluation.csv, results.json, reordered_response.pdf
```

- **Access:** the folder name is a 128-bit random token, which appears in the job URL. It is the only access key;
  there are no accounts.
- **Safe writes:** `job.json` and `state.json` are written to a temporary file and renamed into place, so a reader
  never sees a half-written file.
- **Deletion:** a background thread deletes jobs `RETENTION_HOURS` after upload (24 by default), checking every 10
  minutes. **Delete now** removes a job at once.
- **Processing:** runs in a thread pool inside the web process (`JOB_WORKERS`, default 1).

For one person or a small team this is the right design: a database would add setup and moving parts without
solving a current problem. A database (plus a job queue and object storage) becomes worth it for user accounts,
results kept beyond the retention window, more than one server, or jobs that must survive a restart. See
[ROADMAP.md](ROADMAP.md#when-to-add-a-database).

Details: [ARCHITECTURE.md](ARCHITECTURE.md#job-folder).
