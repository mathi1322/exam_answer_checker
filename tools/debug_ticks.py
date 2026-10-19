"""Draw the tick detector's label boxes on answer-key questions, for tuning.

Usage (inside the dev container, from the project root):
    python -m tools.debug_ticks KEY.pdf OUT_DIR [--only 12,57,130] [--expected AAB... | --golden]

Writes OUT_DIR/q<NNN>.jpg with each located label box (green = chosen,
orange = others, thin grey outline = measurement area) and prints one line per
question with the layout, the three signals and the decision.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw

from app import key_ocr, ticks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key_pdf", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--only", help="comma-separated key question numbers to report")
    ap.add_argument("--expected", help="known key string (one letter per question) to mark mistakes")
    ap.add_argument("--golden", action="store_true", help="use the golden test's key string as --expected")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 2)
    args = ap.parse_args()
    if args.golden:
        from tests.test_golden import EXPECTED_KEY
        args.expected = EXPECTED_KEY

    args.out_dir.mkdir(parents=True, exist_ok=True)
    paths = key_ocr.render_pages(args.key_pdf, args.out_dir / "pages", args.dpi, args.threads)
    pages = key_ocr.ocr_pages(paths, args.threads)
    seg = key_ocr.segment(pages, args.dpi)
    print(f"{len(seg.questions)} questions segmented. Warnings: {seg.warnings or 'none'}")
    only = {int(x) for x in args.only.split(",")} if args.only else None

    stats = {"ok": 0, "wrong": 0, "uncertain": 0}
    for q in seg.questions:
        if only and q.index not in only:
            continue
        imgs = {p.page: ticks.load_page_image(paths[p.page]) for p in q.parts}
        r = ticks.detect_tick(q, pages, imgs)
        exp = args.expected[q.index - 1] if args.expected and len(args.expected) >= q.index else None
        verdict = "uncertain" if r.uncertain else ("ok" if exp in (None, r.answer) else "wrong")
        stats[verdict] += 1
        print(f"Q{q.index:>3} {verdict:<9} got={r.answer or '-'} expected={exp or '?'}  {r.note}  {r.ink}")

        # The options sit on the last part (page) of a question; draw there.
        part = q.parts[-1]
        img = Image.open(paths[part.page]).convert("RGB")
        d = ImageDraw.Draw(img)
        for letter, (pg, x0, y0, x1, y1) in r.labels_found.items():
            if pg != part.page:
                continue
            h = max(10, y1 - y0)
            d.rectangle([x0 - ticks.BOX_LEFT * h, y0 - ticks.BOX_UP * h, x1 + ticks.BOX_RIGHT * h,
                         y1 + ticks.BOX_DOWN * h], outline=(120, 120, 120), width=1)
            colour = (0, 170, 0) if letter == r.answer else (255, 140, 0)
            d.rectangle([x0, y0, x1, y1], outline=colour, width=3)
            d.text((x0 - 28, y0), letter, fill=colour)
        img.crop((0, part.y0, img.width, part.y1)).save(args.out_dir / f"q{q.index:03d}.jpg", quality=85)
    print(stats)


if __name__ == "__main__":
    main()
