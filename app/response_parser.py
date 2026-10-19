"""Response-sheet parsing (TNPSC / TCS iON digital response sheet).

Question number, Question ID, status and chosen option come from the PDF text
layer. The question itself is an embedded image, which is cropped and saved
so it can be OCR'd for matching.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

import pdfplumber
import pytesseract

from .models import BlockPart, ResponseQuestion

LABEL_RE = re.compile(r"^Q\.\s*(\d{1,3})$")
QID_RE = re.compile(r"Question\s*ID\s*:\s*(\d+)", re.I)
STATUS_RE = re.compile(r"Status\s*:\s*([A-Za-z][A-Za-z ]*?)\s*(?=Chosen|Question|$)", re.I | re.M)
CHOSEN_RE = re.compile(r"Chosen\s*Option\s*:\s*([A-E]|--|-)?", re.I)
SUBJECT_RE = re.compile(r"Subject\s*:?\s*(.+)", re.I)

BLOCK_PAD_TOP = 8.0  # pt above the Q.n label
BLOCK_PAD_BOTTOM = 12.0  # pt below the Chosen Option line
MIN_IMAGE_HEIGHT = 20.0  # pt; smaller images are icons, not questions


@dataclass
class ResponseSheet:
    questions: list[ResponseQuestion]
    subject: str = ""
    page_sizes: list[tuple[float, float]] = field(default_factory=list)  # (width, height) pt

    def by_qnum(self) -> dict[int, ResponseQuestion]:
        return {q.qnum: q for q in self.questions}


class ResponseSheetFormat(Protocol):
    name: str

    def detect(self, pdf_path: Path) -> bool: ...

    def parse(self, pdf_path: Path, crops_dir: Optional[Path] = None, dpi: int = 200) -> ResponseSheet: ...


def _find_labels(words: list[dict]) -> list[tuple[int, dict]]:
    out = []
    for i, w in enumerate(words):
        m = LABEL_RE.match(w["text"])
        if m:
            out.append((int(m.group(1)), w))
        elif w["text"] == "Q." and i + 1 < len(words):
            nxt = words[i + 1]
            if nxt["text"].isdigit() and abs(nxt["top"] - w["top"]) < 3:
                out.append((int(nxt["text"]), w))
    return out


class IonResponseSheet:
    name = "TCS iON digital response sheet"

    def detect(self, pdf_path: Path, max_pages: int = 4) -> bool:
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            has_label = False
            for page in pdf.pages[:max_pages]:
                words = page.extract_words()
                has_label = has_label or bool(_find_labels(words))
                text += " ".join(w["text"] for w in words) + "\n"
        return has_label and bool(re.search(r"Chosen\s*Option|Question\s*ID", text, re.I))

    def parse(self, pdf_path: Path, crops_dir: Optional[Path] = None, dpi: int = 200) -> ResponseSheet:
        if crops_dir:
            crops_dir.mkdir(parents=True, exist_ok=True)
        with pdfplumber.open(pdf_path) as pdf:
            page_sizes = [(float(p.width), float(p.height)) for p in pdf.pages]
            subject = ""
            # Document-ordered stream of labels and text lines across all pages.
            labels: list[tuple[int, int, dict]] = []  # (qnum, page, word)
            lines: list[tuple[int, dict]] = []  # (page, line)
            images: list[tuple[int, dict]] = []
            for pi, page in enumerate(pdf.pages):
                words = page.extract_words()
                for qnum, w in _find_labels(words):
                    labels.append((qnum, pi, w))
                for ln in page.extract_text_lines(return_chars=False):
                    lines.append((pi, ln))
                    if pi == 0 and not subject:
                        m = SUBJECT_RE.match(ln["text"].strip())
                        if m:
                            subject = m.group(1).strip()
                for im in page.images:
                    if im["bottom"] - im["top"] >= MIN_IMAGE_HEIGHT:
                        images.append((pi, im))

            labels.sort(key=lambda t: (t[1], t[2]["top"]))
            questions: list[ResponseQuestion] = []
            for li, (qnum, pi, w) in enumerate(labels):
                start = (pi, w["top"])
                end = (labels[li + 1][1], labels[li + 1][2]["top"]) if li + 1 < len(labels) else (len(pdf.pages), 0.0)
                block_lines = [(p, ln) for p, ln in lines if start <= (p, ln["top"] + 1) < end]
                text = "\n".join(ln["text"] for _, ln in block_lines)

                q = ResponseQuestion(qnum=qnum)
                if m := QID_RE.search(text):
                    q.question_id = m.group(1)
                if m := STATUS_RE.search(text):
                    q.status = " ".join(m.group(1).split())
                chosen_line = None
                for p, ln in block_lines:
                    if m := CHOSEN_RE.search(ln["text"]):
                        chosen_line = (p, ln)
                        if m.group(1) and m.group(1) in "ABCDE":
                            q.chosen = m.group(1).upper()
                if q.status.lower().startswith("not"):
                    q.chosen = None

                # Block extent, for the reordered PDF.
                if chosen_line is None:
                    field_lines = [(p, ln) for p, ln in block_lines if re.search(r"Status|Question\s*ID", ln["text"], re.I)]
                    chosen_line = field_lines[-1] if field_lines else (block_lines[-1] if block_lines else (pi, w))
                ep, eln = chosen_line
                bottom = eln["bottom"] + BLOCK_PAD_BOTTOM
                if end[0] == ep:
                    bottom = min(bottom, end[1] - 1)
                top = max(0.0, w["top"] - BLOCK_PAD_TOP)
                if ep == pi:
                    q.parts = [BlockPart(pi, top, min(bottom, page_sizes[pi][1]))]
                else:
                    q.parts = [BlockPart(pi, top, page_sizes[pi][1])]
                    q.parts += [BlockPart(p, 0.0, page_sizes[p][1]) for p in range(pi + 1, ep)]
                    q.parts.append(BlockPart(ep, 0.0, min(bottom, page_sizes[ep][1])))

                # Question image(s): images on the label's page whose nearest label above is this one.
                mine = []
                for ip, im in images:
                    if ip != pi:
                        continue
                    above = [(lw["top"]) for (_, lp, lw) in labels if lp == ip and lw["top"] <= im["top"] + 20]
                    if above and abs(max(above) - w["top"]) < 0.01:
                        mine.append(im)
                if mine:
                    x0 = min(im["x0"] for im in mine)
                    t = min(im["top"] for im in mine)
                    x1 = max(im["x1"] for im in mine)
                    b = max(im["bottom"] for im in mine)
                    pw, ph = page_sizes[pi]
                    q.image_page = pi
                    q.image_bbox = (max(0.0, x0), max(0.0, t), min(pw, x1), min(ph, b))
                    if crops_dir:
                        out = crops_dir / f"resp_{qnum:03d}.png"
                        pdf.pages[pi].crop(q.image_bbox).to_image(resolution=dpi).original.save(out)
                        q.crop_path = str(out)
                questions.append(q)
        return ResponseSheet(questions=questions, subject=subject, page_sizes=page_sizes)


RESPONSE_FORMATS: list[ResponseSheetFormat] = [IonResponseSheet()]


def detect_format(pdf_path: Path) -> Optional[ResponseSheetFormat]:
    for fmt in RESPONSE_FORMATS:
        try:
            if fmt.detect(pdf_path):
                return fmt
        except Exception:
            continue
    return None


def numbering_problems(sheet: ResponseSheet) -> tuple[list[int], list[int]]:
    """Return (duplicate numbers, missing numbers) among the Q.n labels."""
    nums = [q.qnum for q in sheet.questions]
    seen, dups = set(), []
    for n in nums:
        if n in seen:
            dups.append(n)
        seen.add(n)
    missing = [n for n in range(1, max(nums, default=0) + 1) if n not in seen]
    return sorted(set(dups)), missing


def ocr_questions(sheet: ResponseSheet, threads: int = 2, progress=None) -> None:
    """OCR each question crop in place (psm 6, English)."""
    todo = [q for q in sheet.questions if q.crop_path]

    def work(q: ResponseQuestion) -> None:
        q.ocr_text = pytesseract.image_to_string(q.crop_path, lang="eng", config="--psm 6")

    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        for i, _ in enumerate(ex.map(work, todo), 1):
            if progress:
                progress(i, len(todo))
