# Tick detection and tuning

This document explains how `app/ticks.py` decides which option is ticked in the scanned answer key, and how to tune
it when it misbehaves on a real scan.

> **Status:** the detector was developed against synthetic scans (`tests/synth.py`), because the real sample key was
> not available at the time. On synthetic keys it gets every confident detection right, and leaves only very faint
> ticks uncertain (78/80 confident and correct, 2 uncertain, 0 wrong). Expect to tune it on the real key using the
> procedure below.

## The problem

Each question in the key ends with options (A)–(E), laid out either in **one column** or in a **2×2 grid**
(A B / C D) with (E) "Answer not known" on its own line. The correct option has a **hand-drawn pen tick**, usually
right over its label: "(A)" with a tick through it. Three things make this hard:

1. **The ticked label is the one OCR reads worst.** The tick's strokes turn "(A)" into `(Ay`, `ff)`, `(J)` or
   nothing at all. In the synthetic keys, OCR often loses the ticked label entirely. So the position of the one
   label that matters most usually has to be inferred.
2. **Tick strokes are read as junk words.** They stretch the line's bounding box upward and can look like a label in
   the wrong place.
3. **Print differs between labels.** "(B)" has more ink than "(I)" would, there is show-through from the back of the
   page, and some ticks are faint or thin.

## Step 1: locate the labels (`locate_labels`)

The input is the OCR words inside the question's region. The output is a box `[page, x0, y0, x1, y1]` (pixels) for
each of A–E, and a layout note.

**Anchor on (E).** The last line in the question matching the "Answer not known" pattern is the (E) line. Its text
is long and plain, and it is almost always read. `h` is the height of the word "Answer", the unit for every tolerance
below.

**Typical label size.** The width, height and label-to-text gap come from the labels that OCR read cleanly as
`(A)`–`(E)` (the `STRICT_RE` regex). If none were read, the fallbacks are `1.9h` wide, `1.15h` tall and a `0.6h` gap.

**The (E) box** is the word just left of "Answer" if it looks like a label. Otherwise it is a box of typical size,
one gap to the left of "Answer".

**Positions are tracked by bottom, not top.** A tick's junk words pull the line's top edge upward, but the baseline
barely moves. A line's bottom is the median `y1` of its normal-height words.

**Candidates** are words on lines above (E) on the same page that either:

- read cleanly as a letter label (**strict**, letter known), or
- are **labelish**: 5 characters or fewer and containing a bracket or `|`, such as `(Ay`, `ff)`, `|C`. The letter is
  unknown. Statement numbers such as `(1)`, `(iv)` and `(a)` are excluded (`STATEMENT_RE`).

Candidates within `col_tol = max(1.2h, 25 px)` of the (E) label's x form the **left column**. Candidates more than
`3h` to its right form the **right column**.

**Grid or column?** It is a grid if a B or D label was read in the right column, or if a right-column candidate
shares a row (within `0.6h`) with a left-column A/C read or is itself a strict read. Otherwise it is a column.

**Placing the labels in slots** (column: slots A, B, C, D; grid: rows 1 and 2):

1. **Strict reads** go straight into their slot. If two adjacent strict reads are closer together than
   `min_sep = 1.2h`, one is a misread, so the upper one is dropped.
2. **Unread candidates** go, nearest-to-(E) first, into the lowest free slot whose neighbours leave at least
   `min_sep` above and below them.
3. **Still-missing slots are interpolated** between known neighbours, with (E) as the final point. Above the top
   known slot, they are extrapolated using the median slot spacing. The estimate then **snaps** to the nearest
   bottom of a text line that starts near the label column. The ticked option's text line is still there even when
   its label was not read, so snapping usually recovers the exact row.
4. **Grid only:** the right column's x is the median x of right-column B/D reads (or of any right-column
   candidates). Each label is the strict read in its row and column if there is one, otherwise a typical-size box at
   that position.

The layout note in `TickResult.note` records what happened: `column`, `column (inferred)` (at least one label was
placed by inference), `grid`, `grid (no rows read)`, `column (no labels read)` or `no 'Answer not known' line`. If
fewer than 5 boxes come out, the question is uncertain ("could not locate all option labels").

## Step 2: measure ink (`_measure`)

Each label box is expanded into a **measurement area** to catch a tick drawn through or around it. The expansion is
in multiples of the box height `h`:

```
left 0.6h · right 0.35h · up 0.7h · down 0.35h     (BOX_LEFT, BOX_RIGHT, BOX_UP, BOX_DOWN)
```

The area extends further up and left because a tick's short stroke usually starts left of or below the label, and
its long stroke rises to the upper right. Rightwards it stays tight to avoid the option's own text.

Three signals are measured in that area:

| Signal | Pixels counted | Catches | Blind to |
|---|---|---|---|
| **stroke** | Dark pixels (below the local paper level − `FAINT_DELTA`, at most gray 215), dilated 3×3 and grouped into connected components. Only components **taller than 1.3h** or **wider than 1.3 × (box width + h)** count. | A tick, which is one long connected stroke bigger than any printed glyph of the label. Independent of ink density, so it works for faint and thin ticks. | Very small ticks that fit inside the glyph size. |
| **colour** | Pixels with HSV saturation > 90 and gray < 200. | Blue or red pen. Print is black, and the pink paper is light and barely saturated. | Black pen. |
| **dark** | Pixels with gray < `DARK_MAX_GRAY` (175). | Any heavy tick, as excess ink compared with the other labels. | Faint ticks. Noisy where print or show-through varies. |

"Local paper level" is the 75th percentile of gray in the measurement area, so the stroke threshold adapts to the
pink background and uneven scans.

## Step 3: decide (`_decide`, `detect_tick`)

For each signal, take the letter with the highest value (`top`), the runner-up (`second`) and the median of the
others (`med`, excluding `top`). The signal votes for `top` only if:

```
top ≥ ratio × max(med, floor)   and   top ≥ runner_up × max(second, floor)
```

| Signal | ratio | runner_up | floor | Extra condition |
|---|---|---|---|---|
| stroke | 3.0 | 2.0 | 30 px | — |
| colour | 4.0 | 3.0 | 20 px | the highest value is at least `MIN_COLOUR_PX` (80) |
| dark | `TICK_RATIO` (1.6) | `TICK_RUNNER_UP_RATIO` (1.3) | 50 px | — |

The floors stop a tiny absolute number from looking like a big ratio when the other labels have almost no ink.

**The final rule:** if the signals that voted all name **the same letter**, that letter is the answer and the note
lists them (for example `stroke+colour+dark`). If they name different letters, the question is **uncertain** ("signals
disagree: stroke=B, dark=C"). If none voted, it is uncertain ("no single label stands out").

One confident signal is enough. Disagreement is never resolved by majority, because a wrong confident answer is far
worse than an uncertain one: uncertain questions go to a person.

## Step 4: optional AI fallback (`ai_tick`)

Enabled with `AI_FALLBACK=1`, an `ANTHROPIC_API_KEY`, and the checkbox on the upload form. For each question still
uncertain after step 3, the key crop (downscaled to at most 1568 px on its longest side, JPEG) is sent to
`ANTHROPIC_MODEL` with `max_tokens=10` and this prompt:

> This is one multiple-choice question from a scanned answer key. Exactly one option letter (A, B, C, D or E) has a
> handwritten pen tick mark drawn on or next to it. Which option letter has a handwritten tick mark? Reply with a
> single letter, or UNKNOWN if you cannot tell.

Only an exact single letter A–E is accepted, and it gives `method="ai"`. Anything else leaves the question uncertain.
The AI is **never** consulted for questions the image method was confident about. Its answers are labelled "AI" in
the review and results pages, and each one is flagged "Tick read by AI as X: please confirm", so the review screen
always opens for a person to check them.

---

## Tuning on a real key

### 1. Measure first

Run the golden test with `-s` (see [DEVELOPMENT.md](DEVELOPMENT.md#golden-test)). It prints the number of confident,
uncertain and wrong detections, and lists every wrong one. The targets are **≥ 97% correct among confident
detections** and **zero confident errors**. A confident error is much worse than an uncertain question.

### 2. Look at the boxes

The debug tool renders the key, runs detection and draws each question's located labels. A green box is the chosen
label, orange boxes are the others, and the thin grey outline is the measurement area:

```sh
docker run --rm -v "$PWD":/app -w /app exam-answer-checker-dev \
    python -m tools.debug_ticks tests/fixtures/answer_key.pdf debug/real --golden
# add --only 12,57,130 to report just those key questions
# for another key, pass --expected ABCD... (one letter per question) instead of --golden
```

For each question it prints one line, for example:

```
Q 57 uncertain got=- expected=C  column (inferred); no single label stands out  {'dark': {...}, 'colour': {...}, 'stroke': {...}}
```

and writes `debug/real/q057.jpg`. The same numbers are in `results.json` under `questions[].tick` for any processed
job.

### 3. Diagnose with the table below

| Symptom (in the image or the numbers) | Likely cause | What to change |
|---|---|---|
| Boxes are in the wrong rows; one box sits on a Tamil line | Inference snapped to the wrong line | Check `min_sep` (1.2h) and the snap window in `fill()`. For layouts with single-line options (no Tamil line under each), the slot spacing is smaller, so lower `min_sep`. |
| Boxes are misplaced horizontally in grid questions | Wrong right-column x | Check that the B/D labels are read. Inspect `xR` in `locate_labels`. |
| Note says `could not locate all option labels` | No (E) line found, or no labels read at all | Check the OCR of that page. The key may need a higher `RENDER_DPI` (300). |
| Boxes are right, but the ticked label's `stroke` is 0 | The tick lies outside the measurement area, or is too light for the stroke mask | Increase `BOX_UP` / `BOX_LEFT` for ticks drawn beside the label rather than through it. For faint pens, lower `FAINT_DELTA` (for example to 30) so lighter ink counts. |
| `stroke` is non-zero on unticked labels | Underlines, fraction bars or rules inside the area, or text from the line above | Reduce `BOX_UP`, or raise the component size thresholds (1.3h) in `_measure`. |
| `colour` is high on every label | Coloured print, or a strongly tinted scan | Raise the saturation threshold (90) or `MIN_COLOUR_PX`. |
| Many "no single label stands out" while the dark values clearly favour one label | The dark-signal thresholds are too strict | Lower `TICK_RATIO` (for example 1.4) or `TICK_RUNNER_UP_RATIO` (1.2). These are environment variables. |
| Confident but wrong | Something is fooling one signal | Find which signal voted (see the note), and tighten that signal's ratio. Never loosen the other signals to fix a confident error. |

### 4. Re-measure

Re-run the golden test after each change, and also run the synthetic tests (`pytest tests/test_ticks.py`) to make
sure the column, grid and faint-tick cases still pass. Tick detection is evaluated on its own in the golden test, so
you can iterate on it without worrying about matching.

### Where the knobs are

| Knob | Location | Default |
|---|---|---|
| `TICK_RATIO`, `TICK_RUNNER_UP_RATIO` | environment variables | 1.6, 1.3 |
| `BOX_LEFT`, `BOX_RIGHT`, `BOX_UP`, `BOX_DOWN` | `app/ticks.py`, top | 0.6, 0.35, 0.7, 0.35 |
| `DARK_MAX_GRAY`, `FAINT_DELTA`, `MIN_COLOUR_PX` | `app/ticks.py`, top | 175, 40, 80 |
| stroke and colour ratios (3.0/2.0, 4.0/3.0) and floors | `detect_tick` | see the table in step 3 |
| component size thresholds (1.3h) and saturation (90) | `_measure` | — |
| `col_tol`, `min_sep`, fallback label size | `locate_labels` | `max(1.2h, 25)`, `1.2h`, `1.9h × 1.15h` |
| `RENDER_DPI` | environment variable | 200 |
