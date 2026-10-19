# Development guide

This guide is for people changing the code: setting up, running tests, the test fixtures and how to extend the
checker.

## Setup

Everything runs in the dev image, which has the app's dependencies, Tesseract, Poppler, pytest and httpx:

```sh
docker build --build-arg DEV=1 -t exam-answer-checker-dev .
```

Mount the source tree and run commands inside it:

```sh
alias dev='docker run --rm -it -v "$PWD":/app -w /app -e DATA_DIR=/tmp/data -p 8000:8000 exam-answer-checker-dev'

dev python -m pytest -q                                          # all tests (~25 s)
dev uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload     # app with auto-reload
dev python -m tests.synth debug/s 40                             # make a synthetic pair to upload
```

A local virtualenv also works if Python 3.11+, `tesseract` and `pdftoppm` are installed:
`pip install -r requirements-dev.txt`.

## Code layout

```
app/
  main.py            routes (thin: parse, call pipeline/jobs, render)
  jobs.py            job folders, worker thread pool, cleanup
  pipeline.py        process() runs the stages; apply_review(); finalize()
  config.py          Settings from environment variables
  models.py          shared dataclasses
  validate.py        user-facing checks (ValidationError)
  response_parser.py stage 1–2
  key_ocr.py         stage 3–4
  matching.py        stage 5
  ticks.py           stage 6 (+ AI fallback)
  options.py         stage 7
  scoring.py         stage 8
  outputs.py         stage 9
  templates/ static/
tests/
  synth.py           synthetic fixture generator
  conftest.py        session fixtures (synthetic set, parsed sheet, OCR'd key)
  test_*.py
  fixtures/          real sample files for the golden test (not committed)
tools/
  debug_ticks.py     draws located labels and tick signals per question
docs/
```

Conventions:

- **Stage modules are pure.** They take paths and data, return data, and never touch jobs, HTTP or settings
  globals. Thresholds are passed in as arguments. This keeps every stage testable on its own.
- **User-facing failures** raise `validate.ValidationError(message)`. The message is shown verbatim, so write it for
  a non-technical user and include the numbers that help them (for example, both question counts).
- **No personal data in logs.** Log job IDs and stage names only. Never log `ocr_text`, names, IDs or exception
  messages.
- **State changes go through `pipeline`:** `apply_review` updates the overrides, and `finalize` regenerates every
  output from `state.json`. Do not edit the output files in place.

## Tests

| File | Covers |
|---|---|
| `test_response_parser.py` | Question count, Question IDs, chosen options, "Not Answered", block extents, format detection |
| `test_key_ocr.py` | One segment per question, cover page skipped, footers excluded, margin cross-check |
| `test_matching.py` | Normalisation. Recovering a shuffled permutation with 5–15% random character noise. Real-OCR matching. Ambiguity flags |
| `test_ticks.py` | No confident errors, ≥ 90% confident, column and grid layouts, faint ticks, `_decide` |
| `test_options.py` | Same order, reordered (translated), missing ticked label (no false alarm), unreadable |
| `test_scoring.py` | Defaults, negative marking, E as wrong, unanswered as wrong, key E |
| `test_outputs.py` | Each `Q.n` exactly once in the reordered PDF. Blocks taller than half a page. Oversized blocks shrunk. Last page with a single block. CSV format |
| `test_validate.py` | Renamed non-PDF, wrong extension, encrypted, oversized, too many pages, swapped files, numbering gaps, mismatched counts (a key page dropped) |
| `test_pipeline.py` | Review swaps stay one-to-one, answer override and revert, full synthetic end-to-end with scores |
| `test_web.py` | Upload → poll → review (with a swap) → results → downloads → rescore → delete, swapped-file rejection, job-ID path traversal |
| `test_golden.py` | The real sample files. Skipped when absent |

The session fixtures in `conftest.py` generate and OCR one 40-question synthetic set, which most tests share, so the
whole suite takes about 25 seconds.

### Synthetic fixtures (`tests/synth.py`)

`make_fixture(out_dir, n, seed, unanswered, choose_e, grid_frac)` writes three files:

- `synth_response.pdf`: an iON-like response sheet made with reportlab. It has a header table, a wide banner image,
  real text for `Q.n`, `Options A. A …` and the Question Type / ID / Status / Chosen Option box, and one embedded
  JPEG per question (question, a line of fake "Tamil", options A–E).
- `synth_key.pdf`: an image-only scan made with Pillow. It has a cover page, then 1–3 questions per page with margin
  numbers, column or 2×2 grid options, "Tamil" lines, footers, a pink background, show-through, a ±0.5° skew, blur,
  and a pen tick on the correct label. About 15% of the ticks are faint.
- `synth_eval.csv`: the truth, with the same columns as the real `evaluation.csv`.

The "Tamil" is random vowel-less letter strings. It behaves the way real Tamil OCR'd with the English model does, and
the vowel filter removes it.

The fixtures are realistic but **simpler than real scans**: clean fonts, no maths, regular spacing. Passing them is
necessary, not sufficient. Use the golden test for real accuracy.

### Golden test

Copy the real files to `tests/fixtures/` (they are git-ignored, because they contain personal data):
`answer_key.pdf`, `response_sheet.pdf`, `evaluation.csv`. Then:

```sh
dev python -m pytest tests/test_golden.py -s
```

It checks:

- 200 questions in each file;
- the mapping equals `evaluation.csv` exactly, including the spot checks 1→167, 2→54, 3→184, 4→129, 5→80,
  198→196, 199→23 and 200→195;
- tick accuracy against the known 200-letter key string: ≥ 97% among confident detections and **no** confident
  errors. The report is printed with `-s`;
- the final score after a simulated review that fills in only the uncertain ticks: 119 correct, 81 wrong,
  0 unanswered, 178.5 marks, cut-off count 119;
- every `Q.1`–`Q.200` appears exactly once in the reordered PDF.

To measure with the AI fallback (the alias cannot take extra `-e` options, so use the full command):

```sh
docker run --rm -v "$PWD":/app -w /app -e DATA_DIR=/tmp/data -e GOLDEN_AI=1 -e ANTHROPIC_API_KEY=sk-... \
    exam-answer-checker-dev python -m pytest tests/test_golden.py -s
```

`evaluation.csv` is read flexibly. Any column whose name contains "key" and "Q" is the key number, and one containing
"response" and "Q" is the response number.

### Debugging tick detection

```sh
dev python -m tools.debug_ticks tests/fixtures/answer_key.pdf debug/real --golden [--only 12,57]
```

This writes an annotated image per question to `debug/real/` and prints the signals. See
[TICK_DETECTION.md](TICK_DETECTION.md#tuning-on-a-real-key).

## Common changes

### Adding a new exam format

The parsers sit behind two seams.

**A new response-sheet layout** (another portal):

1. In `response_parser.py`, write a class with a `name`, `detect(pdf_path) -> bool` and
   `parse(pdf_path, crops_dir, dpi) -> ResponseSheet`. It must return `ResponseQuestion`s with `qnum`, `question_id`,
   `status`, `chosen`, `parts` (block extents in points from the page top, used for the reordered PDF) and
   `crop_path` (an image of the question, used for OCR and review).
2. Add an instance to `RESPONSE_FORMATS`. The first format whose `detect` returns true is used.
3. Add a synthetic generator or a small real fixture, and a parser test.

If the new sheet has the question text in the text layer, `parse` can fill `ocr_text` directly, and `ocr_questions`
will OCR only questions with a crop. To skip the OCR entirely, guard the call in `pipeline.process`.

**A new answer-key layout:** `key_ocr.segment` assumes every question ends with an "Answer not known" option. For a
paper without option E:

1. Write an alternative segmentation that returns the same `Segmentation` (questions with `text` and `parts`). For
   example, split at margin numbers or at the last option label of each question.
2. Choose between the segmentations in `pipeline.process`, for instance by checking whether the end marker occurs at
   all.
3. `ticks.locate_labels` also anchors on the (E) line. For a four-option paper, anchor on the (D) label instead and
   use the slots `ABC`.

Matching, scoring and outputs are format-independent.

### Changing scoring rules

`scoring.classify` holds the rules and `ScoringConfig` holds the options. A new option needs:

- a field on `ScoringConfig`;
- a form field in `index.html` and `results.html`;
- handling in `main._scoring_from_form`;
- a test in `test_scoring.py`.

`results.json` picks it up through `config`.

### Adding a review flag

Add the condition to `pipeline.flags_for`. It automatically drives `needs_review`, the review page, the `flagged` and
`flags` fields in the outputs, and the CSV's `Flagged` column.

### Changing the reordered PDF

`outputs._layout` decides placement, `_summary_pages` draws the summary and `build_reordered_pdf` merges the blocks.
Keep the text filter (`_block_page`): without it, every question's text appears several times in the PDF. Run
`test_outputs.py` and look at a rendered page:

```sh
dev pdftoppm -r 70 -f 2 -l 2 -png path/to/reordered_response.pdf debug/page
```
