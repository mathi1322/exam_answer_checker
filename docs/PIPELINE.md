# Processing pipeline

This document walks through each processing stage in order: what it reads, how it works, what it produces and how it
fails. The code entry point is `pipeline.process()`. Tick detection (stage 6) has its own document,
[TICK_DETECTION.md](TICK_DETECTION.md).

| # | Stage | Module | Progress share |
|---|---|---|---|
| 0 | Upload validation | `validate.py` (called from `main.py`) | before the job starts |
| 1 | Parse the response sheet | `response_parser.py` | 0–5% |
| 2 | OCR the response questions | `response_parser.ocr_questions` | 5–20% |
| 3 | Render and OCR the answer key | `key_ocr.render_pages`, `ocr_pages` | 20–82% |
| 4 | Split the key into questions | `key_ocr.segment` | 83% |
| 5 | Match questions | `matching.match` | 86% |
| 6 | Detect ticks (plus optional AI) | `ticks.detect_tick`, `ticks.ai_tick` | 88–95% |
| 7 | Check option order | `options.compare` | 96% |
| 8 | Score (at finalize) | `scoring.score` | — |
| 9 | Outputs (at finalize) | `outputs.py` | — |

Stage 3 dominates the run time: OCR of a 118-page scan took about 4 minutes on 2 cores.

---

## 0. Upload validation

`validate.check_pdf()` runs for each file, in this order, and the first failure is shown to the user:

1. The file is present and non-empty.
2. The original filename ends in `.pdf`.
3. The size is at most `MAX_FILE_MB`. The upload is also cut off while streaming, so an oversized file is never
   fully written.
4. The first 1 KB, after any leading whitespace, starts with `%PDF`. This catches other files renamed to `.pdf`.
5. pypdf can open it, and it is **not encrypted**.
6. It has between 1 and `MAX_PAGES` pages.

Then `check_response_format()` asks every registered response-sheet format whether it recognises the file
(`IonResponseSheet.detect`). It must find `Q.<n>` labels **and** "Chosen Option" or "Question ID" text in the first 4
pages. A scanned key uploaded in the response-sheet slot fails here, because it has no text layer.

The other checks need a full parse or the OCR, so they happen inside the job:

| Check | Where | Message shows |
|---|---|---|
| No `Q.n` found, duplicated or missing numbers | after stage 1 (`check_numbering`) | up to 10 offending numbers |
| More than half the questions have no image | after stage 1 | — |
| No question blocks in the key | after stage 4 (`check_counts`) | — |
| Question counts differ | after stage 4 (`check_counts`) | **both counts** |
| Subject not on the key's cover | after stage 4 (`subject_warning`) | a **warning** only, not a failure |

The subject check takes words of 4 or more letters from the response sheet's `Subject` field and warns if fewer than
half of them appear in the OCR text of the key's first two pages.

---

## 1. Parse the response sheet

**Input:** `response.pdf`, a browser-generated PDF with a real text layer.
**Output:** a `ResponseSheet` with one `ResponseQuestion` per `Q.n`, plus a PNG crop of each question image.

Everything except the question text comes from the **text layer**, never from OCR.

1. For every page, pdfplumber gives the words, the text lines (`extract_text_lines`) and the embedded images.
2. **Labels.** A word matching `^Q\.\s*(\d{1,3})$` is a question label. So is the word `Q.` followed by a number on
   the same line.
3. **Blocks.** All labels across all pages are sorted into document order. A question's block is every text line
   from its label up to the next label, which may be on a later page. In that block's text:
   - `Question ID : <digits>` gives the Question ID;
   - `Status : <words>` gives the status, read up to the next field name or the end of the line;
   - `Chosen Option : <A–E | -- | ->` gives the choice.
   - If the status starts with "Not" (for example "Not Answered"), `chosen` is forced to `None`, even if a letter is
     present.
4. **Block extent** (used later for the reordered PDF): from the label's top − 8 pt down to the bottom of the
   "Chosen Option" line + 12 pt, but never past the next label. Without a "Chosen Option" line, the last Status or
   Question ID line is used. A block spanning pages becomes several `BlockPart`s.
5. **Question image.** Images smaller than 20 pt in height are ignored as icons. Each remaining image is assigned to
   the label on the **same page** with the largest top that is at most 20 pt below the image's top: the nearest
   label above it. The header banner and logos on page 1 sit above `Q.1`, so they are never assigned. If a question
   has several images, their union is used.
6. The union box is rendered from the PDF at `RENDER_DPI` (pdfplumber/pypdfium2) and saved as `crops/resp_NNN.png`.
7. The `Subject` field on page 1 is kept for the cover-page warning.

**Failure modes:** a portal that labels questions differently (for example "Question 1") is not detected, and the
upload is rejected. A question whose image sits above its own label would be assigned to the previous question.
This did not happen in the iON sample.

## 2. OCR the response questions

Each crop goes through Tesseract with `--psm 6` (a uniform block of text) and the `eng` language, in parallel. The
output is noisy for the Tamil half of each bilingual question and for maths, which is expected: normalisation in
stage 5 removes most of that noise.

---

## 3. Render and OCR the answer key

**Input:** `key.pdf`, a scan with no text layer.
**Output:** a list of `PageOCR` objects (word boxes per page), plus the margin numbers per page.

1. **Render.** `pdftoppm -r RENDER_DPI -jpeg` renders the pages. The page range is split across `OCR_THREADS`
   pdftoppm processes. JPEG (quality 90) is used instead of PNG because full-colour 200 dpi PNG renders of a 100+ page
   scan take several hundred MB. The renders are deleted when processing finishes.
2. **Main OCR.** Each page is converted to grayscale and run through
   `image_to_data(lang="eng", config="--psm 4")`, which assumes a single column of text of variable sizes. Every
   word keeps its text, confidence, box and `(block, paragraph, line)` IDs, so the words can be grouped back into
   lines.
3. **Margin numbers.** The main pass drops most margin question numbers, especially single digits. So the strip left
   of the question text is OCR'd again on its own with `--psm 6`, keeping tokens like `12.` or `7;` with confidence
   above 30. The left edge of the text is the 10th percentile of the line-start x positions on that page.

## 4. Split the key into questions

**Input:** `PageOCR` for every page.
**Output:** a `Segmentation`: one `KeyQuestion` per question in key order, with its text and its page regions.

**Why this approach:** the margin numbers cannot be trusted, but every question ends with option (E), "Answer not
known". So the OCR text stream is split after each line that matches:

```
answer\s*not\s*kn | nswer\s*n[o0]t\s*k | answer\s*n[o0]t\b | \bnot\s*kn[o0]wn     (case-insensitive)
```

The alternatives tolerate OCR losing the first letter, reading `o` as `0`, or breaking the phrase across words.

Steps:

1. **Lines and footers.** The words on each page are grouped into lines and sorted by vertical centre. A line is a
   **footer** (and excluded) if it matches `turn over`, `^\d{3} – Subject…` (for example
   `504– Statistics, Mathematics and Economics`) or `space for rough work`. It is also a footer if it is shorter
   than 60 characters and lies in the top 2.5% or bottom 4% of the page.
2. **Content band.** For each page, the top of the first and the bottom of the last non-footer line, plus the top
   of the first footer in the lower half (crops never run into the footer).
3. **End markers.** These are the line indices in the cross-page stream that match the pattern. If two adjacent
   lines on the same page both match (option E split into two lines by OCR), only the second counts.
4. **First question start.** It starts on the first page that contains an end marker, so cover pages before it are
   skipped. If the margin number `1` was read on an earlier page (other than page 1), it starts there instead.
5. **Each question** runs from the previous question's end to its own end marker:
   - **text**: the lines in between. Lines above the start y are excluded, which drops the Tamil "answer not known"
     line belonging to the previous question.
   - **region**: from the start y down to the end line's bottom + 70 px (scaled from 200 dpi), so the Tamil line
     under option E is included, but never past the footer. If the region crosses pages, it is split into one
     `KeyPart` per page, using each page's content band.
   - **next start**: the bottom of this region, or the top of the next page's content if this region reached the
     bottom of the page's content.
6. **Cross-check.** Margin numbers found inside a question's region that are within `1..N` are compared with its
   position. Out-of-range numbers are ignored as misreads (`225` for `22.`). A question whose numbers do not include
   its own index goes into `margin_mismatches`, and is flagged for review with a warning.
7. **Crops.** Each question's parts are cut from the full-width page renders, stacked vertically and saved as
   `crops/key_NNN.jpg`.

**Failure modes:**

- An "Answer not known" line that OCR cannot read at all merges two questions. The count is then one short and the
  job fails with both counts. Check the scan quality around the question numbers reported in the warnings.
- A question stem that itself contains "not known" would be split early. This has not been seen in TNPSC papers.

---

## 5. Match questions

**Input:** the key question texts and the response OCR texts.
**Output:** one `MatchPair` per key question, plus the similarity matrix `S` (saved as `similarity.npy`).

1. **Normalise** (`matching.normalise`):
   - remove "answer not known" (with any OCR'd word ending);
   - lowercase, keep alphanumeric tokens only;
   - drop tokens with **no vowel**, unless they are all digits. Tesseract with the English model turns Tamil script
     into vowel-poor junk like `kzrthv`, so this removes most of it while keeping English words and numbers.
2. **Vectorise.** `TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)` is fitted on both
   sets together. Character n-grams inside word boundaries tolerate OCR errors within words: one wrong letter spoils
   only a few n-grams, not the whole word.
3. **Similarity.** `S[i, j]` is the cosine similarity between key question *i* and response question *j*.
4. **Assignment.** `scipy.optimize.linear_sum_assignment(-S)` (the Hungarian algorithm) gives the one-to-one
   assignment with the highest total similarity. This matters: a greedy "best match per row" can give two key
   questions the same response question. The global assignment resolves such conflicts using the other rows.
5. **Flags.** A pair is flagged for review if any of these is true:
   - `score < MATCH_MIN_SCORE` (0.35): weak evidence;
   - `margin < MATCH_MIN_MARGIN` (0.08), where margin = assigned score − best other score in the row: a close
     runner-up;
   - the row's argmax is not the assigned column: the assignment overrode this question's personal best.

In the hand-checked sample the median score was about 0.45, 32 of 200 pairs were flagged and all 200 assignments were
correct. On the synthetic fixtures (clean OCR) the scores are about 0.8 and nothing is flagged for matching.

The review page uses `S` again to suggest the 3 most similar alternatives for each key question.

---

## 6. Detect ticks

See [TICK_DETECTION.md](TICK_DETECTION.md). In short, each key question gets a `TickResult` with a letter, or
`uncertain=True`. If the AI fallback is on, every uncertain question is sent to Claude, and a valid letter in reply
replaces the uncertain result with `method="ai"`. AI answers are used for scoring but still flagged for review
(`flags_for`), so a person confirms each one. An API failure only adds a note; it never fails the job.

---

## 7. Check option order

The sample had identical option order in both documents, but that is not assumed. For each matched pair,
`options.compare(key_text, response_ocr_text)` runs:

1. **Split options.** Find labels with `(?<![A-Za-z0-9])[\(\[]?([A-D])\s?[\.\)\]]\s+`, which matches `(A) `,
   `A. `, `A) ` and `[A] `. Text after "answer not known" is ignored.
   - Only the **last ascending run** of labels is used, because statement text earlier in the question can contain
     things like "A.".
   - An option's text is trusted only if the **next label read is the next letter** (or, for the last one, it is
     D). A ticked label is often unreadable, and then the option before it swallows the missing option's text.
     Comparing that merged text would wrongly suggest a reorder.
   - Each option text is normalised as in stage 5.
2. **Compare.** It builds a similarity matrix with `difflib.SequenceMatcher` over the options both sides could read,
   and takes the best one-to-one pairing (Hungarian algorithm).
3. **Decide.**
   - **`unchecked`**: fewer than 2 options readable on either side.
   - **`same`**: no confident move. A move means: key option X pairs with response option Y ≠ X at similarity ≥ 0.6,
     and X's similarity to response X is below 0.35 (or X is missing on one side).
   - **`reordered`**: there are moves, all 4 options were read on both sides, and every pairing is ≥ 0.6. The
     permutation is stored in `mapping` (with E→E), and the key answer is **translated** to the response's letter
     when scoring.
   - **`suspect`**: there are moves but the evidence is incomplete. It is only flagged; nothing is translated.

Both `reordered` and `suspect` are flagged for review, so a person confirms them. If a reviewer re-matches a question
by hand, its option check no longer applies, and the key letter is used as is.

---

## 8. Scoring

`scoring.score(rows, ScoringConfig)` classifies each question by its (translated) key answer and the chosen option:

| Condition (checked in order) | Result |
|---|---|
| Not answered, `unanswered_policy="wrong"` and a key answer exists | **Wrong** |
| Not answered | **Unanswered** |
| No key answer (still uncertain after review) | **Key unknown** |
| Chosen = key | **Right** |
| Chosen E, `e_policy="wrong"` | **Wrong** |
| Chosen E | **Answer not known** |
| Otherwise | **Wrong** |

```
marks = correct × marks_correct − wrong × marks_wrong
```

The summary contains `total`, `correct`, `wrong`, `chose_e` (all E choices that were not right, whatever the policy),
`unanswered` (all unanswered, whatever the policy), `key_unknown`, `marks`, `max_marks`
(`total × marks_correct`), `cutoff_count` (equal to `correct`) and `config`.

---

## 9. Outputs

`pipeline.finalize()` builds the per-question rows (`build_rows`), scores them and writes three files. The formats
are in [API.md](API.md#output-files). This section covers how the PDF is built.

### Reordered response PDF (`outputs.build_reordered_pdf`)

The goal is to show each original response-sheet block in answer-key order, **without rasterising** it, so the PDF
stays sharp, small and searchable.

1. **Layout** (`_layout`): the blocks are placed top to bottom on A4 pages with 28 pt margins, each preceded by a
   14 pt result bar and followed by a 10 pt gap.
   - A block is scaled down to the usable width if needed. It is never scaled up.
   - A block taller than a whole page is shrunk to fit one page.
   - When the next block (with its bar) does not fit, a new page starts. A multi-page block's later parts have no
     bar.
2. **Summary pages** (reportlab): the scores, then the table of all questions, 50 rows × 4 columns per page.
3. **Overlay pages** (reportlab): the coloured bars and page numbers, one page per layout page.
4. **Placing each block** (pypdf), on top of its overlay page:
   - **Text filtering** (`_block_page`): a copy of the source page is made whose content stream has every
     text-showing operator (`Tj`, `TJ`, `'`, `"`) **removed when its baseline falls outside the block**. The
     operator walk tracks `q`/`Q`/`cm` and the text matrix (`BT`, `Tm`, `Td`, `TD`, `T*`, `TL`). Without this,
     clipping would hide the neighbouring blocks visually, but their text would still be extractable. Every question
     would then appear 2–3 times when the PDF is searched or copied.
   - **Clipping**: the copy's `cropbox` is set to the block's region, and
     `PageObject.merge_transformed_page(copy, Transformation().scale(s).translate(tx, ty))` places it. pypdf clips
     the merged content to the source cropbox, so images and vector graphics from neighbouring blocks are not drawn.
5. The images in the source page are shared XObjects, so the output stays small even though one source page feeds
   several output pages.

The golden test checks the result by extracting `Q.n` from the text layer of every page after the summary: each of
the 200 numbers must appear exactly once.
