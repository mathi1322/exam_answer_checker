import collections
import re

from pypdf import PdfReader

from app import outputs
from app.models import BlockPart
from app.scoring import ScoringConfig, score


def _rows(sheet, order):
    by_q = sheet.by_qnum()
    rows = [{"key_index": i + 1, "resp_qnum": n, "question_id": by_q[n].question_id, "key_answer": "A",
             "chosen": by_q[n].chosen, "match_score": 0.5, "flagged": False} for i, n in enumerate(order)]
    return rows, score(rows, ScoringConfig())


def _qlabels(pdf_path, skip=1):
    c = collections.Counter()
    for p in PdfReader(str(pdf_path)).pages[skip:]:
        c.update(re.findall(r"(?m)^\s*Q\.(\d+)\s*$", p.extract_text()))
    return c


def test_reordered_pdf_each_question_once(tmp_path, synth, synth_sheet):
    order = [q.qnum for q in reversed(synth_sheet.questions)]
    rows, summary = _rows(synth_sheet, order)
    parts = {q.qnum: q.parts for q in synth_sheet.questions}
    out = tmp_path / "r.pdf"
    n = outputs.build_reordered_pdf(synth.response_pdf, rows, parts, summary, {"subject": "x"}, out)
    assert n == len(PdfReader(str(out)).pages) > 1
    labels = _qlabels(out)
    assert set(map(int, labels)) == set(order) and set(labels.values()) == {1}
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "Answer check summary" in text and "Cut-off count" in text


def test_layout_tall_blocks_and_single_last_page():
    rows = [{"resp_qnum": n, "key_index": n, "key_answer": "A", "chosen": "A", "result": "Right"} for n in (1, 2, 3)]
    tall = {1: [BlockPart(0, 50, 500)], 2: [BlockPart(0, 50, 500)], 3: [BlockPart(0, 0, 1600)]}
    pages = outputs._layout(rows, tall, {0: 595.0})
    assert len(pages) == 3  # each block is taller than half a page
    assert all(len(p) == 1 for p in pages)
    last = pages[-1][0]
    assert (1600 * last.scale) <= outputs.PAGE_H - 2 * outputs.MARGIN  # shrunk to fit one page
    short = {n: [BlockPart(0, 0, 100)] for n in (1, 2, 3)}
    assert [len(p) for p in outputs._layout(rows, short, {0: 595.0})] == [3]


def test_csv(tmp_path):
    rows = [{"key_index": 1, "resp_qnum": 5, "question_id": "123", "key_answer": "B", "chosen": None,
             "result": "Unanswered", "match_score": 0.41234, "flagged": True}]
    p = tmp_path / "e.csv"
    outputs.write_csv(rows, p)
    lines = p.read_text().splitlines()
    assert lines[0] == ",".join(outputs.CSV_COLUMNS)
    assert lines[1] == "1,5,123,B,,Unanswered,0.412,Yes"

