"""Answer-key rendering, OCR and segmentation into questions.

The scanned key has no text layer. Pages are rendered with pdftoppm, OCR'd
with Tesseract (psm 4, word boxes kept) and the text stream is split after
every "Answer not known" line, since every question ends with option (E).
"""
from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytesseract
from PIL import Image

from .models import KeyPart, KeyQuestion

os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # we parallelise across pages instead

END_RE = re.compile(r"answer\s*not\s*kn|nswer\s*n[o0]t\s*k|answer\s*n[o0]t\b|\bnot\s*kn[o0]wn", re.I)
FOOTER_RES = [
    re.compile(r"turn\s*over", re.I),
    re.compile(r"^\W*\d{3}\s*[-–—~]+\s*[A-Za-z]"),  # "504– Statistics, Mathematics and Economics"
    re.compile(r"space\s+for\s+rough\s+work", re.I),
]
MARGIN_NUM_RE = re.compile(r"^(\d{1,3})[.,:;)]?$")
TAIL_PX_AT_200 = 70  # extra crop below the "Answer not known" line, for the Tamil line


@dataclass
class Word:
    text: str
    conf: float
    x0: int
    y0: int
    x1: int
    y1: int
    line: tuple[int, int, int]  # (block, par, line)

    @property
    def h(self) -> int:
        return self.y1 - self.y0


@dataclass
class Line:
    page: int
    words: list[Word]
    footer: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> int:
        return min(w.x0 for w in self.words)

    @property
    def y0(self) -> int:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> int:
        return max(w.y1 for w in self.words)


@dataclass
class PageOCR:
    index: int
    width: int
    height: int
    words: list[Word] = field(default_factory=list)
    margin: list[tuple[int, int, int]] = field(default_factory=list)  # (number, y0, y1) read in the margin strip

    def lines(self) -> list[Line]:
        groups: dict[tuple, list[Word]] = {}
        for w in self.words:
            groups.setdefault(w.line, []).append(w)
        out = [Line(self.index, sorted(ws, key=lambda w: w.x0)) for ws in groups.values()]
        out.sort(key=lambda ln: (ln.y0 + ln.y1) / 2)
        for ln in out:
            ln.footer = _is_footer(ln, self.height)
        return out


@dataclass
class Segmentation:
    questions: list[KeyQuestion]
    lines: list[Line]
    warnings: list[str] = field(default_factory=list)
    margin_mismatches: list[int] = field(default_factory=list)  # key indices


def _is_footer(ln: Line, page_h: int) -> bool:
    t = ln.text.strip()
    if any(r.search(t) for r in FOOTER_RES):
        return True
    # A short line in the extreme top/bottom band is a running header/footer or page number.
    return (ln.y0 > 0.96 * page_h or ln.y1 < 0.025 * page_h) and len(t) < 60


# ---------------------------------------------------------------- render + OCR

def render_pages(pdf_path: Path, out_dir: Path, dpi: int = 200, threads: int = 2, n_pages: Optional[int] = None) -> list[Path]:
    """Render every page to JPEG with pdftoppm, splitting the page range across processes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if n_pages is None:
        from pypdf import PdfReader
        n_pages = len(PdfReader(str(pdf_path)).pages)
    threads = max(1, min(threads, n_pages))
    chunk = -(-n_pages // threads)
    ranges = [(s + 1, min(n_pages, s + chunk)) for s in range(0, n_pages, chunk)]

    def run(rng):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(rng[0]), "-l", str(rng[1]), "-jpeg",
                        "-jpegopt", "quality=90", str(pdf_path), str(out_dir / "page")], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(run, ranges))
    pages = sorted(out_dir.glob("page-*.jpg"), key=lambda p: int(p.stem.split("-")[-1]))
    if len(pages) != n_pages:
        raise RuntimeError(f"Rendered {len(pages)} of {n_pages} answer-key pages")
    return pages


def ocr_page(path: Path, index: int) -> PageOCR:
    img = Image.open(path).convert("L")
    data = pytesseract.image_to_data(img, lang="eng", config="--psm 4", output_type=pytesseract.Output.DICT)
    page = PageOCR(index, img.width, img.height)
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        page.words.append(Word(text, float(data["conf"][i]), x, y, x + w, y + h,
                               (data["block_num"][i], data["par_num"][i], data["line_num"][i])))
    page.margin = _ocr_margin(img, page)
    return page


def _text_left(page: PageOCR) -> Optional[int]:
    starts = sorted(ln.x0 for ln in page.lines() if not ln.footer)
    return starts[len(starts) // 10] if len(starts) >= 3 else None


def _ocr_margin(img: Image.Image, page: PageOCR) -> list[tuple[int, int, int]]:
    """Main-pass OCR tends to drop short margin numbers, so read the margin strip on its own."""
    left = _text_left(page)
    if not left or left < 40:
        return []
    strip = img.crop((0, 0, left - 8, img.height))
    data = pytesseract.image_to_data(strip, lang="eng", output_type=pytesseract.Output.DICT,
                                     config="--psm 6")
    out = []
    for i, text in enumerate(data["text"]):
        m = MARGIN_NUM_RE.match((text or "").strip())
        if m and float(data["conf"][i]) > 30:
            y = data["top"][i]
            out.append((int(m.group(1)), y, y + data["height"][i]))
    return out


def ocr_pages(paths: list[Path], threads: int = 2, progress=None) -> list[PageOCR]:
    results: list[Optional[PageOCR]] = [None] * len(paths)

    def work(i):
        results[i] = ocr_page(paths[i], i)

    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        for done, _ in enumerate(ex.map(work, range(len(paths))), 1):
            if progress:
                progress(done, len(paths))
    return results  # type: ignore[return-value]


# ---------------------------------------------------------------- segmentation

def _margin_numbers(page: PageOCR, lines: list[Line]) -> list[tuple[int, int]]:
    """Question numbers printed in the left margin, as (number, y0)."""
    out = [(n, y0) for n, y0, _ in page.margin]
    left = _text_left(page)
    for ln in lines:  # numbers the main pass did read
        w = ln.words[0]
        m = MARGIN_NUM_RE.match(w.text)
        if m and not ln.footer and left and w.x1 <= left + 5 and not any(abs(y - w.y0) < 15 for _, y in out):
            out.append((int(m.group(1)), w.y0))
    return out


def segment(pages: list[PageOCR], dpi: int = 200) -> Segmentation:
    tail = round(TAIL_PX_AT_200 * dpi / 200)
    page_lines = [p.lines() for p in pages]
    margins = {p.index: _margin_numbers(p, page_lines[p.index]) for p in pages}
    content = {}
    for p in pages:
        body = [ln for ln in page_lines[p.index] if not ln.footer]
        footer_top = min([ln.y0 for ln in page_lines[p.index] if ln.footer and ln.y0 > p.height / 2], default=p.height)
        content[p.index] = (min([ln.y0 for ln in body], default=0), max([ln.y1 for ln in body], default=p.height), footer_top)

    stream = [ln for pl in page_lines for ln in pl if not ln.footer]
    ends = [i for i, ln in enumerate(stream) if END_RE.search(ln.text)]
    # Two end markers on adjacent lines are one option (E) split by OCR; keep the last.
    ends = [e for k, e in enumerate(ends) if not (k + 1 < len(ends) and ends[k + 1] == e + 1
                                                   and stream[ends[k + 1]].page == stream[e].page)]
    seg = Segmentation(questions=[], lines=stream)
    if not ends:
        seg.warnings.append("No 'Answer not known' lines were found in the answer key.")
        return seg

    # Where question 1 starts: the page of the first end marker, or an earlier
    # (non-cover) page if the margin number "1" was read there.
    first_end = stream[ends[0]]
    start_page = first_end.page
    for pi in range(1, first_end.page):
        if any(n == 1 for n, _ in margins[pi]):
            start_page = pi
            break
    start_idx = next(i for i, ln in enumerate(stream) if ln.page >= start_page)
    start_pos = (start_page, max(0, content[start_page][0] - 10))

    for k, e in enumerate(ends):
        end_ln = stream[e]
        seg_lines = [ln for ln in stream[start_idx:e + 1]
                     if (ln.page, ln.y1) > (start_pos[0], start_pos[1] + 2)]
        q = KeyQuestion(index=k + 1, text="\n".join(ln.text for ln in seg_lines))
        ps, ys = start_pos
        pe = end_ln.page
        ye = min(end_ln.y1 + tail, content[pe][2] - 2, pages[pe].height)
        if ps == pe:
            q.parts = [KeyPart(ps, int(ys), int(ye))]
        else:
            q.parts = [KeyPart(ps, int(ys), int(min(content[ps][1] + 10, content[ps][2] - 2)))]
            q.parts += [KeyPart(p, int(max(0, content[p][0] - 10)), int(min(content[p][1] + 10, content[p][2] - 2)))
                        for p in range(ps + 1, pe)]
            q.parts.append(KeyPart(pe, int(max(0, content[pe][0] - 10)), int(ye)))

        # Cross-check against the margin numbers read inside this question's region.
        nums = [n for p in range(ps, pe + 1) for n, y in margins[p]
                if 1 <= n <= len(ends) and any(part.page == p and part.y0 - 5 <= y <= part.y1 for part in q.parts)]
        if nums:
            q.margin_number = nums[0]
            if q.index not in nums:
                seg.margin_mismatches.append(q.index)
        seg.questions.append(q)

        # Next question starts after the Tamil line that follows "Answer not known".
        start_idx = e + 1
        if ye >= content[pe][1] - 5 and pe + 1 < len(pages):
            start_pos = (pe + 1, max(0, content[pe + 1][0] - 10))
        else:
            start_pos = (pe, int(ye))

    if seg.margin_mismatches:
        seg.warnings.append(
            f"{len(seg.margin_mismatches)} key question(s) have a margin number that disagrees with their "
            f"position: {', '.join(map(str, seg.margin_mismatches[:15]))}"
            + (" …" if len(seg.margin_mismatches) > 15 else ""))
    return seg


def save_crops(questions: list[KeyQuestion], page_paths: list[Path], crops_dir: Path) -> None:
    """Save each question's region (stitched across pages if needed) as a JPEG."""
    crops_dir.mkdir(parents=True, exist_ok=True)
    cache: dict[int, Image.Image] = {}

    def page(i):
        if i not in cache:
            if len(cache) > 3:
                cache.clear()
            cache[i] =Image.open(page_paths[i]).convert("RGB")
        return cache[i]

    for q in questions:
        pieces = [page(p.page).crop((0, p.y0, page(p.page).width, p.y1)) for p in q.parts if p.y1 > p.y0]
        if not pieces:
            continue
        out = Image.new("RGB", (max(im.width for im in pieces), sum(im.height for im in pieces)), "white")
        y = 0
        for im in pieces:
            out.paste(im, (0, y))
            y += im.height
        path = crops_dir / f"key_{q.index:03d}.jpg"
        out.save(path, quality=85)
        q.crop_path = str(path)


def cover_text(pages: list[PageOCR], n: int = 2) -> str:
    return "\n".join(" ".join(w.text for w in p.words) for p in pages[:n])


def words_in_parts(pages: list[PageOCR], q: KeyQuestion) -> list[tuple[int, Word]]:
    out = []
    for part in q.parts:
        for w in pages[part.page].words:
            cy = (w.y0 + w.y1) / 2
            if part.y0 <= cy <= part.y1:
                out.append((part.page, w))
    return out
