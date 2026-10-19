"""Downloadable outputs: evaluation.csv, results.json and the reordered response-sheet PDF."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import ContentStream, NameObject, RectangleObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .models import BlockPart
from .scoring import CHOSE_E, KEY_UNKNOWN, RIGHT, UNANSWERED

CSV_COLUMNS = ["Answer key Q", "Response sheet Q", "Question ID", "Key answer", "Chosen option", "Result",
               "Match score", "Flagged"]

GREEN = colors.HexColor("#1e7b34")
RED = colors.HexColor("#b3261e")
GREY = colors.HexColor("#5f6368")
AMBER = colors.HexColor("#9a6700")


def result_colour(result: str):
    return {RIGHT: GREEN, CHOSE_E: AMBER, UNANSWERED: GREY, KEY_UNKNOWN: GREY}.get(result, RED)


def write_csv(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([r["key_index"], r["resp_qnum"], r["question_id"], r["key_answer"] or "", r["chosen"] or "",
                        r["result"], f'{r["match_score"]:.3f}', "Yes" if r["flagged"] else "No"])


def write_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- reordered PDF

PAGE_W, PAGE_H = A4
MARGIN = 28.0
BAR_H = 14.0
GAP = 10.0


@dataclass
class Placement:
    part: BlockPart
    y_top: float  # distance from the output page top to the top of the block
    scale: float
    bar: Optional[tuple[str, object]] = None  # (text, colour) drawn just above the block


def _layout(rows: list[dict], parts_by_q: dict[int, list[BlockPart]], src_widths: dict[int, float]) -> list[list[Placement]]:
    usable_w = PAGE_W - 2 * MARGIN
    usable_h = PAGE_H - 2 * MARGIN
    pages: list[list[Placement]] = [[]]
    y = MARGIN
    for r in rows:
        for i, part in enumerate(parts_by_q[r["resp_qnum"]]):
            bar = None
            if i == 0:
                verdict = {RIGHT: "CORRECT"}.get(r["result"], r["result"].upper())
                text = (f'Answer key Q{r["key_index"]} = Response Q.{r["resp_qnum"]}    Key: {r["key_answer"] or "?"}    '
                        f'Chosen: {r["chosen"] or "-"}    {verdict}')
                bar = (text, result_colour(r["result"]))
            h_bar = BAR_H + 2 if bar else 0
            scale = min(1.0, usable_w / src_widths[part.page])
            h = (part.bottom - part.top) * scale
            if h + h_bar > usable_h:  # taller than a page: shrink to fit
                scale *= (usable_h - h_bar) / h
                h = (part.bottom - part.top) * scale
            if y + h_bar + h > PAGE_H - MARGIN and pages[-1]:
                pages.append([])
                y = MARGIN
            pages[-1].append(Placement(part, y + h_bar, scale, bar))
            y += h_bar + h + GAP
    return pages


def _summary_pages(c: canvas.Canvas, rows: list[dict], summary: dict, meta: dict) -> int:
    per_col, cols = 50, 4
    per_page = per_col * cols
    n_pages = max(1, -(-len(rows) // per_page))
    for p in range(n_pages):
        y = PAGE_H - MARGIN - 10
        if p == 0:
            c.setFont("Helvetica-Bold", 15)
            c.drawString(MARGIN, y, "Answer check summary")
            y -= 18
            c.setFont("Helvetica", 9)
            if meta.get("subject"):
                c.drawString(MARGIN, y, f'Subject: {meta["subject"]}')
                y -= 13
            cfg = summary["config"]
            c.drawString(MARGIN, y, (
                f'Questions: {summary["total"]}    Correct: {summary["correct"]}    Wrong: {summary["wrong"]}    '
                f'Answer not known (E): {summary["chose_e"]}    Unanswered: {summary["unanswered"]}'
                + (f'    Key unknown: {summary["key_unknown"]}' if summary["key_unknown"] else "")))
            y -= 13
            c.setFont("Helvetica-Bold", 10)
            c.drawString(MARGIN, y, (
                f'Marks: {summary["marks"]:g} / {summary["max_marks"]:g}    Cut-off count (correct answers): '
                f'{summary["cutoff_count"]}'))
            y -= 12
            c.setFont("Helvetica", 7.5)
            c.setFillColor(GREY)
            c.drawString(MARGIN, y, (f'{cfg["marks_correct"]:g} per correct answer, {cfg["marks_wrong"]:g} deducted per '
                                     f'wrong answer. Pages after this one show each response-sheet question in '
                                     f'answer-key order.'))
            c.setFillColor(colors.black)
            y -= 16
        chunk = rows[p * per_page:(p + 1) * per_page]
        col_w = (PAGE_W - 2 * MARGIN) / cols
        row_h = min(12.0, (y - MARGIN) / (per_col + 1))
        for ci in range(cols):
            x = MARGIN + ci * col_w
            c.setFont("Helvetica-Bold", 6.5)
            for dx, head in ((0, "Key Q"), (24, "Resp Q"), (52, "Key"), (72, "Chosen"), (100, "Result")):
                c.drawString(x + dx, y, head)
            for ri, r in enumerate(chunk[ci * per_col:(ci + 1) * per_col]):
                yy = y - (ri + 1) * row_h
                c.setFont("Helvetica", 6.5)
                c.setFillColor(colors.black)
                c.drawString(x, yy, str(r["key_index"]))
                c.drawString(x + 24, yy, f'Q.{r["resp_qnum"]}')
                c.drawString(x + 52, yy, r["key_answer"] or "?")
                c.drawString(x + 72, yy, r["chosen"] or "-")
                c.setFillColor(result_colour(r["result"]))
                c.drawString(x + 100, yy, {RIGHT: "Right", CHOSE_E: "E", KEY_UNKNOWN: "Key ?", UNANSWERED: "Blank"}.get(r["result"], r["result"]))
            c.setFillColor(colors.black)
        c.showPage()
    return n_pages


def _mul(a: list[float], b: list[float]) -> list[float]:
    """PDF matrix product a x b, matrices as [a b c d e f]."""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


_IDENTITY = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
_SHOW_OPS = {b"Tj", b"TJ", b"'", b'"'}


def _block_page(reader: PdfReader, src: PageObject, y_lo: float, y_hi: float) -> PageObject:
    """A copy of `src` whose text outside y_lo..y_hi is removed.

    Clipping alone hides the neighbouring blocks but leaves their text in the
    text layer (so copy/search would find other questions). This walks the
    content stream, tracks the text position and drops text-showing operators
    whose baseline falls outside the block.
    """
    content = ContentStream(src.get_contents(), reader)
    ctm, stack = list(_IDENTITY), []
    tm = tlm = list(_IDENTITY)
    leading = 0.0
    kept = []
    for operands, op in content.operations:
        if op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            ctm = stack.pop() if stack else list(_IDENTITY)
        elif op == b"cm":
            ctm = _mul([float(x) for x in operands], ctm)
        elif op == b"BT":
            tm = tlm = list(_IDENTITY)
        elif op == b"Tm":
            tm = tlm = [float(x) for x in operands]
        elif op in (b"Td", b"TD"):
            tx, ty = float(operands[0]), float(operands[1])
            if op == b"TD":
                leading = -ty
            tm = tlm = _mul([1, 0, 0, 1, tx, ty], tlm)
        elif op == b"TL":
            leading = float(operands[0])
        elif op in (b"T*", b"'", b'"'):
            tm = tlm = _mul([1, 0, 0, 1, 0, -leading], tlm)
        if op in _SHOW_OPS:
            y = _mul(tm, ctm)[5]
            if not (y_lo - 2 <= y <= y_hi + 2):
                if op in (b"'", b'"'):
                    kept.append(([], b"T*"))  # keep the line advance, drop the text
                continue
        kept.append((operands, op))
    content.operations = kept
    page = PageObject(reader)
    for key, value in src.items():
        if key != "/Contents":
            page[NameObject(key)] = value
    page.replace_contents(content)
    return page


def build_reordered_pdf(response_pdf: Path, rows: list[dict], parts_by_q: dict[int, list[BlockPart]],
                        summary: dict, meta: dict, out_path: Path) -> int:
    """Write the summary page(s) plus every response block in answer-key order. Returns the page count."""
    reader = PdfReader(str(response_pdf))
    widths = {i: float(p.mediabox.width) for i, p in enumerate(reader.pages)}
    layout = _layout(rows, parts_by_q, widths)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Reordered response sheet")
    n_summary = _summary_pages(c, rows, summary, meta)
    for pi, placements in enumerate(layout, 1):
        for pl in placements:
            if pl.bar:
                text, colour = pl.bar
                c.setFillColor(colour)
                c.rect(MARGIN, PAGE_H - pl.y_top + 2, PAGE_W - 2 * MARGIN, BAR_H, stroke=0, fill=1)
                c.setFillColor(colors.white)
                c.setFont("Helvetica-Bold", 8.5)
                c.drawString(MARGIN + 5, PAGE_H - pl.y_top + 6, text)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 7)
        c.drawRightString(PAGE_W - MARGIN, MARGIN / 2, f"{pi} / {len(layout)}")
        c.showPage()
    c.save()
    buf.seek(0)
    base = PdfReader(buf)

    writer = PdfWriter()
    for i in range(n_summary):
        writer.add_page(base.pages[i])
    for pi, placements in enumerate(layout):
        dest = writer.add_page(base.pages[n_summary + pi])  # merge into the writer's copy (pypdf 7 requires it)
        for pl in placements:
            mb = reader.pages[pl.part.page].mediabox
            y_hi = float(mb.top) - pl.part.top
            y_lo = float(mb.top) - pl.part.bottom
            src = _block_page(reader, reader.pages[pl.part.page], y_lo, y_hi)
            # merge_transformed_page clips to the source cropbox, so only this block is drawn.
            src.cropbox = RectangleObject([float(mb.left), y_lo, float(mb.right), y_hi])
            tx = MARGIN - float(mb.left) * pl.scale
            ty = (PAGE_H - pl.y_top - (pl.part.bottom - pl.part.top) * pl.scale) - y_lo * pl.scale
            dest.merge_transformed_page(src, Transformation().scale(pl.scale, pl.scale).translate(tx, ty))
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return len(writer.pages)
