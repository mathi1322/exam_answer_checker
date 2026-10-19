"""Synthetic fixtures that mimic the TNPSC answer key and the iON response sheet.

Used by the tests when the real sample files are not present, and to exercise
edge cases (unanswered questions, grid option layouts, faint ticks).
"""
from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

WORDS = (
    "mean median mode variance deviation regression correlation sample population census index price "
    "inflation demand supply elasticity utility marginal cost revenue profit monopoly market equilibrium "
    "budget deficit fiscal monetary policy bank reserve rate interest capital labour wage rent tax tariff "
    "export import trade balance growth income poverty employment agriculture industry service sector "
    "matrix determinant integral derivative function limit series sequence vector probability random "
    "variable binomial poisson normal distribution hypothesis estimator unbiased efficient sufficient "
    "consistent quartile percentile skewness kurtosis moment frequency histogram ogive chart table "
    "planning commission finance literacy rural urban village district state union council report "
    "theorem axiom prime factor ratio proportion percentage average weighted harmonic geometric "
    "arithmetic progression triangle circle radius area volume surface angle parallel perpendicular"
).split()

TEMPLATES = [
    "Which of the following is the correct {a} of the {b} {c} in a {d} {e}?",
    "The {a} of {b} and {c} is known as the {d} {e} of the {f}.",
    "If the {a} of a {b} is {n1} and the {c} is {n2}, then the {d} is",
    "Consider the following statements about {a} {b}: the {c} {d} is always {e}. Which is correct?",
    "In the theory of {a} {b}, the {c} curve shows the relation between {d} and {e}.",
    "Evaluate the {a} of x{n1} with respect to {b} when the {c} equals {n2}.",
    "Who introduced the concept of {a} {b} in the study of {c} {d}?",
    "The {a} {b} test is used to compare the {c} of two {d} {e} samples.",
]


@dataclass
class SynthQuestion:
    key_index: int
    resp_qnum: int
    question_id: str
    stem: str
    options: list[str]
    answer: str
    chosen: str | None
    grid: bool
    faint: bool


@dataclass
class SynthFixture:
    key_pdf: Path
    response_pdf: Path
    csv_path: Path
    questions: list[SynthQuestion] = field(default_factory=list)

    @property
    def key_string(self) -> str:
        return "".join(q.answer for q in self.questions)


def _font(size: int):
    return ImageFont.load_default(size=size)


def _tamil_noise(rng: random.Random, n: int = 6) -> str:
    """Stands in for Tamil text: OCR of the real thing gives vowel-less junk like this."""
    cons = "kstnmprlvgdhjbcfxz"
    return " ".join("".join(rng.choice(cons) for _ in range(rng.randint(3, 7))) for _ in range(n))


def _make_questions(rng: random.Random, n: int, unanswered: int, choose_e: int, grid_frac: float) -> list[SynthQuestion]:
    perm = list(range(1, n + 1))
    rng.shuffle(perm)
    qs = []
    for k in range(1, n + 1):
        t = rng.choice(TEMPLATES)
        w = rng.sample(WORDS, 6)
        stem = t.format(a=w[0], b=w[1], c=w[2], d=w[3], e=w[4], f=w[5], n1=rng.randint(2, 99), n2=rng.randint(2, 99))
        opts = [" ".join(rng.sample(WORDS, rng.randint(1, 3))) for _ in range(4)]
        ans = rng.choice("ABCD")
        chosen = ans if rng.random() < 0.6 else rng.choice([c for c in "ABCD" if c != ans])
        qs.append(SynthQuestion(k, perm[k - 1], f"6406531{rng.randint(10000, 99999)}", stem, opts, ans, chosen,
                                grid=rng.random() < grid_frac, faint=rng.random() < 0.15))
    for q in rng.sample(qs, unanswered + choose_e)[:unanswered]:
        q.chosen = None
    for q in [q for q in qs if q.chosen is not None][:choose_e]:
        q.chosen = "E"
    return qs


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return out + ([line] if line else [])


# ---------------------------------------------------------------- response sheet

def _question_image(q: SynthQuestion, rng: random.Random) -> Image.Image:
    f = _font(26)
    lines = _wrap(q.stem, 60) + [_tamil_noise(rng, 5)]
    for letter, opt in zip("ABCD", q.options):
        lines.append(f"{letter}. {opt}")
    lines.append("E. Answer not known")
    img = Image.new("RGB", (1300, 40 + 36 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((20, 20 + 36 * i), ln, fill="black", font=f)
    return img


def make_response_pdf(qs: list[SynthQuestion], path: Path, rng: random.Random) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setProducer("Skia/PDF m120")
    W, H = A4
    banner = Image.new("RGB", (1295, 120), (30, 60, 140))
    c.drawImage(ImageReader(banner), 30, H - 80, width=W - 60, height=55)
    y = H - 100
    for label, val in [("Participant ID", "TEST0001"), ("Participant Name", "TEST CANDIDATE"),
                       ("Test Center", "Test Centre"), ("Test Date", "01/01/2026"), ("Test Time", "9:30 AM"),
                       ("Subject", "Statistics, Mathematics and Economics"), ("Application Number", "0000"),
                       ("Registration Number", "0000")]:
        c.setFont("Helvetica", 9)
        c.drawString(40, y, f"{label}")
        c.drawString(200, y, val)
        y -= 14
    y -= 10
    for q in sorted(qs, key=lambda q: q.resp_qnum):
        img = _question_image(q, rng)
        iw = 440.0
        ih = iw * img.height / img.width
        block_h = ih + 110
        if y - block_h < 40:
            c.showPage()
            y = H - 40
        c.setFont("Helvetica-Bold", 10)
        c.drawString(36, y - 12, f"Q.{q.resp_qnum}")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        buf.seek(0)
        c.drawImage(ImageReader(buf), 80, y - ih, width=iw, height=ih)
        yy = y - ih - 16
        c.setFont("Helvetica", 9)
        c.drawString(80, yy, "Options  A. A   B. B   C. C   D. D   E. E")
        yy -= 14
        c.rect(380, yy - 50, 170, 62)
        status = "Answered" if q.chosen else "Not Answered"
        for text in ["Question Type : MCQ", f"Question ID : {q.question_id}", f"Status : {status}",
                     f"Chosen Option : {q.chosen or '--'}"]:
            c.drawString(386, yy, text)
            yy -= 14
        y = yy - 30
    c.save()


# ---------------------------------------------------------------- answer key

KEY_DPI = 200
PAGE_W, PAGE_H = int(8.27 * KEY_DPI), int(11.69 * KEY_DPI)
BG = (250, 230, 234)


def _draw_tick(d: ImageDraw.ImageDraw, x: int, y: int, h: int, rng: random.Random, faint: bool) -> None:
    color = rng.choice([(20, 30, 120), (25, 25, 25), (40, 60, 160)])
    if faint:
        color = tuple(min(255, c + 110) for c in color)
    width = rng.randint(2, 3) if faint else rng.randint(3, 6)
    p0 = (x - int(0.1 * h) + rng.randint(-4, 4), y + int(0.45 * h))
    p1 = (x + int(0.55 * h) + rng.randint(-4, 4), y + int(1.05 * h))
    p2 = (x + int(1.9 * h) + rng.randint(-6, 6), y - int(0.7 * h) + rng.randint(-6, 6))
    d.line([p0, p1, p2], fill=color, width=width, joint="curve")


def _key_page_images(qs: list[SynthQuestion], rng: random.Random, per_page: tuple[int, int] = (1, 3)) -> list[Image.Image]:
    f = _font(32)
    lh = 46
    pages: list[Image.Image] = []

    def new_page(cover=False):
        img = Image.new("RGB", (PAGE_W, PAGE_H), BG)
        d = ImageDraw.Draw(img)
        # show-through from the reverse side
        for i in range(8):
            d.text((200 + rng.randint(0, 200), 300 + i * 220), _tamil_noise(rng, 4), fill=(236, 220, 224), font=f)
        if not cover:
            d.text((PAGE_W // 2 - 380, PAGE_H - 120), "504- Statistics, Mathematics and Economics", fill="black", font=_font(28))
            d.text((PAGE_W - 300, PAGE_H - 70), "[Turn over", fill="black", font=_font(28))
        return img, d

    img, d = new_page(cover=True)
    d.text((300, 300), "STATISTICS, MATHEMATICS AND ECONOMICS", fill="black", font=_font(44))
    d.text((300, 400), "PAPER II  Code 504", fill="black", font=_font(40))
    for i, ln in enumerate(_wrap("Read the instructions carefully before answering. This booklet contains "
                                 "two hundred questions. Each question carries equal marks.", 70)):
        d.text((200, 600 + i * lh), ln, fill="black", font=f)
    pages.append(img)

    qi = 0
    while qi < len(qs):
        img, d = new_page()
        y = 140
        for _ in range(rng.randint(*per_page)):
            if qi >= len(qs):
                break
            q = qs[qi]
            body = _wrap(q.stem, 62)
            need = lh * (len(body) + 2 + (4 if q.grid else 8) + 3)
            if y + need > PAGE_H - 200 and y > 140:
                break
            d.text((60, y), f"{q.key_index}.", fill="black", font=f)
            for ln in body:
                d.text((150, y), ln, fill="black", font=f)
                y += lh
            d.text((150, y), _tamil_noise(rng, 6), fill="black", font=f)
            y += lh + 10
            labels = {}
            if q.grid:
                for row in range(2):
                    for col in range(2):
                        letter = "ABCD"[row * 2 + col]
                        x = 150 + col * 700
                        labels[letter] = (x, y)
                        d.text((x, y), f"({letter})  {q.options[row * 2 + col]}", fill="black", font=f)
                        d.text((x + 70, y + lh), _tamil_noise(rng, 2), fill="black", font=f)
                    y += 2 * lh + 6
            else:
                for i, letter in enumerate("ABCD"):
                    labels[letter] = (150, y)
                    d.text((150, y), f"({letter})  {q.options[i]}", fill="black", font=f)
                    d.text((220, y + lh), _tamil_noise(rng, 3), fill="black", font=f)
                    y += 2 * lh + 6
            labels["E"] = (150, y)
            d.text((150, y), "(E)  Answer not known", fill="black", font=f)
            y += lh
            d.text((220, y), _tamil_noise(rng, 3), fill="black", font=f)
            y += lh + 50
            lx, ly = labels[q.answer]
            _draw_tick(d, lx, ly, 34, rng, q.faint)
            qi += 1
        pages.append(img)

    out = []
    for img in pages:
        img = img.rotate(rng.uniform(-0.5, 0.5), resample=Image.BICUBIC, fillcolor=BG)
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        out.append(img)
    return out


def make_key_pdf(qs: list[SynthQuestion], path: Path, rng: random.Random) -> None:
    imgs = _key_page_images(qs, rng)
    imgs[0].save(path, "PDF", resolution=KEY_DPI, save_all=True, append_images=imgs[1:])


def write_csv(qs: list[SynthQuestion], path: Path) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Answer key Q", "Response sheet Q", "Question ID", "Key answer", "Chosen option", "Result"])
        for q in qs:
            res = "Unanswered" if q.chosen is None else ("Right" if q.chosen == q.answer else "Wrong")
            w.writerow([q.key_index, q.resp_qnum, q.question_id, q.answer, q.chosen or "", res])


def make_fixture(out_dir: Path, n: int = 24, seed: int = 0, unanswered: int = 0, choose_e: int = 0,
                 grid_frac: float = 0.4) -> SynthFixture:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    qs = _make_questions(rng, n, unanswered, choose_e, grid_frac)
    fx = SynthFixture(out_dir / "synth_key.pdf", out_dir / "synth_response.pdf", out_dir / "synth_eval.csv", qs)
    make_response_pdf(qs, fx.response_pdf, rng)
    make_key_pdf(qs, fx.key_pdf, rng)
    write_csv(qs, fx.csv_path)
    return fx


if __name__ == "__main__":
    import sys
    fx = make_fixture(Path(sys.argv[1] if len(sys.argv) > 1 else "synth_out"), n=int(sys.argv[2]) if len(sys.argv) > 2 else 24)
    print(fx.key_pdf, fx.response_pdf, fx.key_string)
