"""Detect which option label carries the hand-drawn tick in the answer key.

1. Locate the option labels (A)..(E) from the OCR word boxes. The (E) line
   ("Answer not known") anchors the layout; labels the OCR mangled or dropped
   (a ticked label often reads as "(Ay", "ff)" or vanishes) are inferred from
   the column / 2x2-grid layout.
2. Measure ink in a box around each label: dark pixels, and separately
   "coloured" pixels (pen ink is often blue or red, print is black).
3. The tick is on the label whose ink clearly exceeds the others. Anything
   ambiguous is marked uncertain, and optionally sent to Claude.
"""
from __future__ import annotations

import base64
import io
import re
import statistics
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from .key_ocr import END_RE, PageOCR, Word, words_in_parts
from .models import KeyQuestion, TickResult

STRICT_RE = re.compile(r"^[\(\[\{|]?([A-E])[\)\]\}|][.,:;]?$")
LABELISH_RE = re.compile(r"^.{0,2}[\(\)\[\]\{\}|].{0,3}$")
STATEMENT_RE = re.compile(r"^[\(\[]?(\d{1,2}|[ivxIVX]{1,4}|[a-e])[\)\]]?[.,]?$")

# Measurement box around a label, in multiples of the label height. The tick
# usually starts left/below the label and its long stroke runs up and right.
BOX_LEFT, BOX_RIGHT, BOX_UP, BOX_DOWN = 0.6, 0.35, 0.7, 0.35
DARK_MAX_GRAY = 175
FAINT_DELTA = 40  # gray levels below the local background that count as (faint) ink
MIN_COLOUR_PX = 80


def _labelish(w: Word) -> bool:
    t = w.text
    return len(t) <= 5 and bool(LABELISH_RE.match(t)) and not STATEMENT_RE.match(t)


def _strict(w: Word) -> Optional[str]:
    m = STRICT_RE.match(w.text)
    return m.group(1) if m else None


def _lines(words: list[tuple[int, Word]]) -> list[list[tuple[int, Word]]]:
    groups: dict[tuple, list[tuple[int, Word]]] = {}
    for p, w in words:
        groups.setdefault((p, w.line), []).append((p, w))
    out = [sorted(g, key=lambda t: t[1].x0) for g in groups.values()]
    out.sort(key=lambda g: (g[0][0], statistics.median((w.y0 + w.y1) / 2 for _, w in g)))
    return out


def locate_labels(words: list[tuple[int, Word]]) -> tuple[dict[str, list[int]], str]:
    """Return ({letter: [page, x0, y0, x1, y1]}, layout note). Boxes are in page pixels.

    Positions are tracked by label *bottom*: tick strokes that OCR as junk
    words pull line tops upward, but rarely move the baseline.
    """
    # Step 1: the (E) "Answer not known" line is the anchor. It is the last such
    # line in the question, and its text is almost always read correctly.
    lines = _lines(words)
    end_i = max((i for i, ln in enumerate(lines) if END_RE.search(" ".join(w.text for _, w in ln))), default=None)
    if end_i is None:
        return {}, "no 'Answer not known' line"
    end_ln = lines[end_i]
    page = end_ln[0][0]
    ans_idx = next((k for k, (_, w) in enumerate(end_ln) if re.match(r"(?i)answer|nswer", w.text)), 0)
    ans = end_ln[ans_idx][1]
    h = max(12, ans.h)

    # Step 2: typical label width/height and label-to-text gap, from labels that
    # OCR'd cleanly as "(A)" etc. Fall back to multiples of the text height h.
    strict_words = [w for _, w in words if _strict(w)]
    lw = statistics.median([w.x1 - w.x0 for w in strict_words]) if strict_words else 1.9 * h
    lh = statistics.median([w.h for w in strict_words]) if strict_words else 1.15 * h
    gap = statistics.median([0.6 * h] + [
        nxt.x0 - w.x1 for ln in lines for (_, w), (_, nxt) in zip(ln, ln[1:]) if _strict(w)])

    def bottom(ln) -> float:
        ys = [w.y1 for _, w in ln if 0.6 * h <= w.h <= 1.8 * h] or [w.y1 for _, w in ln]
        return statistics.median(ys)

    def box_at(x: float, yb: float) -> list[int]:
        return [page, int(x), int(yb - lh), int(x + lw), int(yb)]

    # Step 3: the (E) label is the word just left of "Answer", or, if OCR lost it,
    # a box of typical size placed one gap to the left of "Answer".
    boxes: dict[str, list[int]] = {}
    if ans_idx > 0 and _labelish(end_ln[ans_idx - 1][1]):
        e = end_ln[ans_idx - 1][1]
        boxes["E"] = [page, e.x0, e.y0, e.x1, e.y1]
    else:
        boxes["E"] = box_at(ans.x0 - gap - lw, bottom(end_ln))
    xE, yE = boxes["E"][1], bottom(end_ln)
    # col_tol: how far (px) a label may sit from the E label's x and still be in
    # the left column. min_sep: two option rows are at least this far apart.
    col_tol = max(1.2 * h, 25)
    min_sep = 1.2 * h

    # Step 4: candidate labels on lines above the E line (same page). A strict read
    # ("(B)") carries its letter; a "labelish" word ("(Ay", "ff)") is a label whose
    # letter is unknown. Left-column candidates align with E; right-column ones sit
    # well to the right, which only happens in a 2x2 grid.
    above = [ln for ln in lines[:end_i] if ln[0][0] == page]
    cands = []  # (word, letter|None, y_bottom)
    for ln in above:
        yb = bottom(ln)
        for _, w in ln:
            if _strict(w) or _labelish(w):
                cands.append((w, _strict(w), w.y1 if _strict(w) else yb))
    left = [c for c in cands if abs(c[0].x0 - xE) <= col_tol and c[2] < yE - 0.5 * h]
    right = [c for c in cands if c[0].x0 > xE + 3 * h and c[2] < yE - 0.5 * h]
    # Grid if a B/D label was read on the right, or a right-column label shares a
    # row with a left-column one.
    is_grid = any(c[1] in ("B", "D") for c in right) or any(
        abs(r[2] - l[2]) < 0.6 * h for r in right for l in left if l[1] in ("A", "C") or r[1])
    # Bottoms of text lines that start near the label column: an inferred label
    # snaps to one of these, because the ticked option's text line still exists
    # even when its label was not read.
    line_bottoms = sorted({bottom(ln) for ln in above if min(w.x0 for _, w in ln) <= xE + lw + gap + col_tol})

    def snap(y: float, lo: float, hi: float) -> float:
        near = [s for s in line_bottoms if lo < s < hi]
        return min(near, key=lambda s: abs(s - y)) if near else y

    def place(slots: str, pool, ys: dict[str, float], limit: float) -> None:
        """Fill ys for `slots` (top to bottom) from strict reads, then unread labels."""
        for w, letter, yb in pool:
            if letter and letter in slots and letter not in ys:
                ys[letter] = yb
        # Drop strict reads that are out of order (misreads such as "(B)" for "(8)").
        for a, b in zip(slots, slots[1:]):
            if a in ys and b in ys and ys[b] - ys[a] < min_sep:
                ys.pop(a)
        for w, letter, yb in sorted(pool, key=lambda c: -c[2]):  # nearest the E line first
            if letter and letter in slots:
                continue
            free = [s for s in reversed(slots) if s not in ys]
            for s in free:
                i = slots.index(s)
                lo = max([ys[o] for o in slots[:i] if o in ys], default=-1e9)
                hi = min([ys[o] for o in slots[i + 1:] if o in ys] + [limit])
                if lo + min_sep <= yb <= hi - min_sep:
                    ys[s] = yb
                    break

    def fill(slots: str, ys: dict[str, float], limit: float) -> None:
        """Interpolate/extrapolate the slots still missing, using the limit as the next slot."""
        pts = sorted((slots.index(s), y) for s, y in ys.items()) + [(len(slots), limit)]
        steps = [(y2 - y1) / (i2 - i1) for (i1, y1), (i2, y2) in zip(pts, pts[1:])]
        step = statistics.median(steps) if steps else 2.5 * h
        for s in slots:
            if s in ys:
                continue
            i = slots.index(s)
            prev = [(j, y) for j, y in pts if j < i]
            nxt = [(j, y) for j, y in pts if j > i]
            if prev:
                (j1, y1), (j2, y2) = prev[-1], nxt[0]
                est = y1 + (y2 - y1) * (i - j1) / (j2 - j1)
            else:
                j2, y2 = nxt[0]
                est = y2 - (j2 - i) * step
            lo = prev[-1][1] if prev else est - step
            ys[s] = snap(est, lo + 0.5 * min_sep, nxt[0][1] - 0.5 * min_sep)

    # Step 5 (grid): find the two option rows (A B / C D) the same way as column
    # slots, then place each label in its row and column.
    if is_grid:
        row_pool = [(w, {"A": "1", "B": "1", "C": "2", "D": "2"}.get(l or ""), yb) for w, l, yb in left + right]
        rows: dict[str, float] = {}
        place("12", row_pool, rows, yE)
        if not rows:
            return boxes, "grid (no rows read)"
        fill("12", rows, yE)
        xr_reads = [c[0].x0 for c in right if c[1] in ("B", "D")] or [c[0].x0 for c in right]
        xR = statistics.median(xr_reads)
        for r, letters in (("1", "AB"), ("2", "CD")):
            for col, letter in enumerate(letters):
                pool = left if col == 0 else right
                hit = [c[0] for c in pool if abs(c[2] - rows[r]) < 0.6 * h and (c[1] in (None, letter))]
                boxes[letter] = ([page, hit[0].x0, hit[0].y0, hit[0].x1, hit[0].y1]
                                 if hit and _strict(hit[0]) else box_at(xE if col == 0 else xR, rows[r]))
        return boxes, "grid"

    # Step 5 (column): place strict reads, then unread labels in the free slots
    # consistent with their position, then interpolate whatever is still missing.
    ys: dict[str, float] = {}
    place("ABCD", left, ys, yE)
    if not ys:
        return boxes, "column (no labels read)"
    inferred = len(ys) < 4
    fill("ABCD", ys, yE)
    for letter, yb in ys.items():
        hit = [c[0] for c in left if c[1] == letter and abs(c[2] - yb) < 1]
        boxes[letter] = [page, hit[0].x0, hit[0].y0, hit[0].x1, hit[0].y1] if hit else box_at(xE, yb)
    return boxes, "column (inferred)" if inferred else "column"


def _measure(img_rgb: np.ndarray, gray: np.ndarray, box: list[int]) -> tuple[float, float, float]:
    """(dark pixels, coloured pixels, pixels in stroke-like components) around one label."""
    _, x0, y0, x1, y1 = box
    h = max(10, y1 - y0)
    H, W = gray.shape
    xa, xb = max(0, int(x0 - BOX_LEFT * h)), min(W, int(x1 + BOX_RIGHT * h))
    ya, yb = max(0, int(y0 - BOX_UP * h)), min(H, int(y1 + BOX_DOWN * h))
    g = gray[ya:yb, xa:xb]
    if g.size == 0:
        return 0.0, 0.0, 0.0
    dark = float((g < DARK_MAX_GRAY).sum())
    # Stroke mask relative to the local paper colour, so faint pen ticks still count.
    bg = float(np.percentile(g, 75))
    mask = (g < min(bg - FAINT_DELTA, 215)).astype(np.uint8)
    hsv = cv2.cvtColor(np.ascontiguousarray(img_rgb[ya:yb, xa:xb]), cv2.COLOR_RGB2HSV)
    colour = float(((hsv[..., 1] > 90) & (g < 200)).sum())
    # A tick is one connected stroke larger than any printed glyph of the label.
    n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, np.ones((3, 3), np.uint8)), connectivity=8)
    stroke = float(sum(stats[i, cv2.CC_STAT_AREA] for i in range(1, n)
                       if stats[i, cv2.CC_STAT_HEIGHT] > 1.3 * h or stats[i, cv2.CC_STAT_WIDTH] > 1.3 * (x1 - x0 + h)))
    return dark, colour, stroke


def _decide(values: dict[str, float], ratio: float, runner_up: float, floor: float = 1.0) -> Optional[str]:
    if len(values) < 3:
        return None
    ranked = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
    (top, v1), (_, v2) = ranked[0], ranked[1]
    med = statistics.median(v for k, v in values.items() if k != top)
    if v1 >= ratio * max(med, floor) and v1 >= runner_up * max(v2, floor):
        return top
    return None


def detect_tick(q: KeyQuestion, pages: list[PageOCR], page_images: dict[int, tuple[np.ndarray, np.ndarray]],
                ratio: float = 1.6, runner_up: float = 1.3) -> TickResult:
    words = words_in_parts(pages, q)
    boxes, layout = locate_labels(words)
    res = TickResult(labels_found=boxes, note=layout)
    if len(boxes) < 5:
        res.note = f"could not locate all option labels ({layout})"
        return res
    dark, colour, stroke = {}, {}, {}
    for letter, box in boxes.items():
        rgb, gray = page_images[box[0]]
        dark[letter], colour[letter], stroke[letter] = _measure(rgb, gray, box)
    res.ink = {"dark": {k: round(v) for k, v in dark.items()},
               "colour": {k: round(v) for k, v in colour.items()},
               "stroke": {k: round(v) for k, v in stroke.items()}}

    votes = {
        "stroke": _decide(stroke, 3.0, 2.0, floor=30),
        "colour": _decide(colour, 4.0, 3.0, floor=MIN_COLOUR_PX / 4) if max(colour.values()) >= MIN_COLOUR_PX else None,
        "dark": _decide(dark, ratio, runner_up, floor=50),
    }
    picked = {v for v in votes.values() if v}
    if len(picked) == 1:
        res.answer = picked.pop()
        res.uncertain = False
        res.note += "; " + "+".join(k for k, v in votes.items() if v)
    elif len(picked) > 1:
        res.note += "; signals disagree: " + ", ".join(f"{k}={v}" for k, v in votes.items() if v)
    else:
        res.note += "; no single label stands out"
    return res


def load_page_image(path) -> tuple[np.ndarray, np.ndarray]:
    rgb = np.asarray(Image.open(path).convert("RGB"))
    return rgb, cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


# ---------------------------------------------------------------- optional AI fallback

AI_PROMPT = ("This is one multiple-choice question from a scanned answer key. Exactly one option letter "
             "(A, B, C, D or E) has a handwritten pen tick mark drawn on or next to it. Which option letter "
             "has a handwritten tick mark? Reply with a single letter, or UNKNOWN if you cannot tell.")


def ai_tick(crop_path: str, model: str) -> Optional[str]:
    """Ask Claude which option is ticked. Returns a letter, or None."""
    import anthropic

    img = Image.open(crop_path).convert("RGB")
    if max(img.size) > 1568:
        img.thumbnail((1568, 1568))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=10,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": AI_PROMPT},
        ]}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip().upper()
    return text if text in ("A", "B", "C", "D", "E") else None
