# HTTP routes and output formats

This is the reference for scripting against the service and for consuming its output files. The web UI uses the same
routes. There is no separate JSON API: uploads and review submissions are ordinary HTML form posts.

All `/jobs/{job_id}/…` routes return **404** if the job does not exist, has expired or was deleted. The job ID is
the only credential, so keep job URLs private.

## Routes

| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/` | Upload form | HTML |
| POST | `/upload` | Start a job | **303** to `/jobs/{id}`, or **400** HTML form with an error message |
| GET | `/jobs/{id}` | Progress page | HTML. Redirects (303) to `/review` or `/results` when the job is ready |
| GET | `/jobs/{id}/status` | Poll the job status | JSON (below) |
| GET | `/jobs/{id}/review` | Review page | HTML. Redirects to `/jobs/{id}` until processing is finished |
| POST | `/jobs/{id}/review` | Submit corrections, then score and build the outputs | **303** to `/results`. **409** if the job is not in `review` or `done` |
| GET | `/jobs/{id}/results` | Results page | HTML. Redirects to `/jobs/{id}` until `done` |
| POST | `/jobs/{id}/rescore` | Recalculate with other scoring settings | **303** to `/results`. **400** on invalid values, **409** if not `done` |
| GET | `/jobs/{id}/download/{name}` | Download an output file | The file. `name` is one of `evaluation.csv`, `results.json`, `reordered_response.pdf` |
| GET | `/jobs/{id}/crop/key/{k}` | Answer-key question *k* (key order) as JPEG | image/jpeg |
| GET | `/jobs/{id}/crop/resp/{n}` | Response question `Q.n` as PNG | image/png |
| POST | `/jobs/{id}/delete` | Delete the job folder now | HTML confirmation. **409** while queued or processing |
| GET | `/healthz` | Liveness: are `tesseract` and `pdftoppm` installed? | `{"ok": true}` (200), or 503 |
| GET | `/static/…` | CSS | — |

### `POST /upload`

`multipart/form-data`:

| Field | Required | Default | Notes |
|---|---|---|---|
| `answer_key` | yes | — | PDF file |
| `response_sheet` | yes | — | PDF file |
| `marks_correct` | no | 1.5 | > 0 |
| `marks_wrong` | no | 0 | ≥ 0. Marks *deducted* per wrong answer |
| `e_policy` | no | `separate` | `separate` or `wrong` |
| `unanswered_policy` | no | `separate` | `separate` or `wrong` |
| `use_ai` | no | off | Any value turns it on. Ignored unless the server has the AI fallback enabled |

The upload checks run synchronously (see [PIPELINE.md](PIPELINE.md#0-upload-validation)). The OCR runs in the
background after the redirect.

### `GET /jobs/{id}/status`

```json
{"status": "processing", "stage": "Reading answer key pages (37/118)", "progress": 0.41, "message": ""}
```

| `status` | Meaning | Next step |
|---|---|---|
| `queued` | Waiting for a worker | keep polling |
| `processing` | Running. `stage` and `progress` (0–1) update as it goes | keep polling |
| `review` | Finished. Something needs a person's look | open `/jobs/{id}/review` |
| `done` | Results ready | open `/results` or download |
| `invalid` | Rejected by a check that needs the full parse (for example, the question counts differ) | `message` explains why |
| `error` | Unexpected failure or server restart | `message` is generic. See the server log |

### `POST /jobs/{id}/review`

`application/x-www-form-urlencoded`. Every field is optional. Questions not included keep their current values.

| Field | Value | Effect |
|---|---|---|
| `match_<k>` | Any text containing a response question number, such as `Q.167`, `167` or `Q.167 — The mean of…` | Matches key question *k* to that response question. The question that previously held it gets *k*'s old one (a swap), so the mapping stays one-to-one. Numbers that are not real `Q.n` are ignored |
| `answer_<k>` | `A`–`E`, or empty | Sets key question *k*'s answer. A value equal to the detected answer, or empty, removes any manual override |

Example, scripting a whole run:

```sh
JOB=$(curl -s -o /dev/null -w '%{redirect_url}' \
  -F answer_key=@answer_key.pdf -F response_sheet=@response_sheet.pdf -F marks_correct=1.5 \
  http://localhost:8000/upload)
until curl -s "$JOB/status" | grep -qE '"(review|done|invalid|error)"'; do sleep 5; done
curl -s "$JOB/status"
curl -s -X POST -d 'answer_38=A' "$JOB/review" -o /dev/null      # confirm the review (with one fix)
curl -s -O "$JOB/download/results.json"
curl -s -X POST "$JOB/delete" -o /dev/null
```

### `POST /jobs/{id}/rescore`

The same scoring fields as the upload (`marks_correct` is required). This rewrites all three output files, and stores
the new settings in the job.

---

## Output files

### `evaluation.csv`

UTF-8, with a header row and one row per question in **answer-key order**.

| Column | Example | Meaning |
|---|---|---|
| `Answer key Q` | `1` | Question number in the answer key (its position) |
| `Response sheet Q` | `167` | The candidate's question number (`Q.167`) |
| `Question ID` | `640653123456` | From the response sheet |
| `Key answer` | `A` | Correct option **in the response sheet's letters** (translated if options were reordered). Empty if unknown |
| `Chosen option` | `C` | The candidate's choice. Empty if not answered |
| `Result` | `Right` | `Right`, `Wrong`, `Answer not known`, `Unanswered` or `Key unknown` (see [scoring](PIPELINE.md#8-scoring)) |
| `Match score` | `0.452` | Text similarity of the pair, 0–1, 3 decimals |
| `Flagged` | `No` | `Yes` if the question was flagged for review for any reason |

### `results.json`

```jsonc
{
  "summary": {
    "total": 200, "correct": 119, "wrong": 81, "chose_e": 0, "unanswered": 0, "key_unknown": 0,
    "marks": 178.5, "max_marks": 300.0, "cutoff_count": 119,
    "config": {"marks_correct": 1.5, "marks_wrong": 0.0, "e_policy": "separate", "unanswered_policy": "separate"}
  },
  "warnings": ["..."],                 // same as shown on the pages
  "ai_fallback_used": false,
  "reordered_pdf_pages": 101,
  "questions": [                       // answer-key order
    {
      "key_index": 1,                  // answer key Q
      "resp_qnum": 167,                // response sheet Q.n
      "question_id": "640653123456",
      "key_answer_in_key": "A",        // letter as ticked in the key
      "key_answer": "A",               // letter in response-sheet terms (after option-order translation)
      "key_answer_method": "image",    // image | ai | manual
      "chosen": "A",                   // null if not answered
      "status": "Answered",            // as printed on the response sheet
      "result": "Right",
      "match_score": 0.452,
      "match_manual": false,           // true if a person changed this match
      "flagged": false,
      "flags": [],                     // human-readable reasons, as shown on the review page
      "tick": {
        "answer": "A", "uncertain": false, "method": "image", "ai_answer": null,
        "note": "column (inferred); stroke+dark",
        "ink": {"dark": {"A": 642, ...}, "colour": {...}, "stroke": {...}}
      },
      "option_check": {"status": "same", "mapping": {}, "detail": ""},
      "key_margin_number": 1           // number read in the key's margin, or null
    }
  ]
}
```

`tick` holds the **automatic** detection. If a reviewer overrode the answer, `key_answer_method` is `manual` and
`key_answer` holds their letter, while `tick.answer` still shows what was detected. The meaning of `ink` and `note` is
explained in [TICK_DETECTION.md](TICK_DETECTION.md).

### `reordered_response.pdf`

- **Summary page(s)**: the score lines, then a table of every question (50 rows × 4 columns per page). Columns: key
  Q, response Q, key answer, chosen, result (`Right`, `Wrong`, `E`, `Blank` or `Key ?`).
- **Question pages**: every response-sheet block in answer-key order, unchanged and still vector. Above each block is
  a bar reading `Answer key Q<k> = Response Q.<n>    Key: X    Chosen: Y    <RESULT>`. Green means correct, red
  wrong, amber "answer not known", grey unanswered or key unknown. Page numbers "i / N" count only the question
  pages.
- Each question's text appears exactly once in the text layer, so searching the PDF for `Q.57` finds exactly one
  block.
