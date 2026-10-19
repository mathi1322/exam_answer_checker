"""Golden end-to-end test against the real TNPSC sample files (section 9a).

Put answer_key.pdf, response_sheet.pdf and evaluation.csv in tests/fixtures/.
Skipped when they are absent. Takes several minutes (OCR of ~118 pages).
Run with `-s` to see the tick-detection report.
"""
import csv
import os
import re
import shutil
from pathlib import Path

import pytest
from pypdf import PdfReader

from app import pipeline
from app.config import Settings
from app.scoring import ScoringConfig

FIX = Path(__file__).parent / "fixtures"
FILES = [FIX / "answer_key.pdf", FIX / "response_sheet.pdf", FIX / "evaluation.csv"]

EXPECTED_KEY = (
    "AABACBADCBDAADAABDCAAAACCAAABABCCCACABBCCBAACCCDDB"
    "BBBDCACBBBADDBCCCDBACCCDBBCDBCBDBBBABBCADADDCCAABB"
    "ABADBAADABCBCADCBCDCACACBBCADDCBAACDDBCDAAACBCCDCB"
    "BDBABCCADDAABCBBADCCCDBABBBACCCCAABBDDCADADBDBBBAC"
)

pytestmark = pytest.mark.skipif(not all(f.exists() for f in FILES), reason="real sample files not in tests/fixtures/")


def _expected_mapping() -> dict[int, int]:
    with open(FIX / "evaluation.csv", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    kcol = next(c for c in rows[0] if re.search(r"key", c, re.I) and re.search(r"\bq", c, re.I))
    rcol = next(c for c in rows[0] if re.search(r"response", c, re.I) and re.search(r"\bq", c, re.I))
    return {int(re.search(r"\d+", r[kcol]).group()): int(re.search(r"\d+", r[rcol]).group()) for r in rows}


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    job = tmp_path_factory.mktemp("golden")
    shutil.copy(FILES[0], job / "key.pdf")
    shutil.copy(FILES[1], job / "response.pdf")
    use_ai = bool(os.environ.get("GOLDEN_AI")) and bool(os.environ.get("ANTHROPIC_API_KEY"))
    state = pipeline.process(job, Settings(), use_ai=use_ai)
    return job, state


def test_counts_and_mapping(golden):
    _, state = golden
    assert len(state["response"]["questions"]) == 200
    assert len(state["key"]["questions"]) == 200
    expected = _expected_mapping()
    got = pipeline.current_mapping(state)
    wrong = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    assert not wrong, f"{len(wrong)} wrong matches (got, expected): {dict(list(wrong.items())[:10])}"
    for k, q in ((1, 167), (2, 54), (3, 184), (4, 129), (5, 80), (198, 196), (199, 23), (200, 195)):
        assert got[k] == q


def test_tick_detection(golden):
    _, state = golden
    ticks = [q["tick"] for q in state["key"]["questions"]]
    confident = [(i, t) for i, t in enumerate(ticks) if not t["uncertain"]]
    errors = [(i + 1, t["answer"], EXPECTED_KEY[i]) for i, t in confident if t["answer"] != EXPECTED_KEY[i]]
    acc = 1 - len(errors) / max(1, len(confident))
    print(f"\nTick detection: {len(confident)} confident, {200 - len(confident)} uncertain, "
          f"{len(errors)} wrong among confident ({acc:.1%} correct). AI used: {state['ai_used']}")
    if errors:
        print("Wrong (key Q, got, expected):", errors)
    assert acc >= 0.97
    assert not errors, "every tick error should have been caught as uncertain"


def test_final_score_after_review(golden):
    job, state = golden
    # A reviewer fixes whatever was left uncertain; matching must not need fixing.
    answers = {k: EXPECTED_KEY[k - 1] for k in range(1, 201) if pipeline.detected_key_answer(state, k) is None}
    pipeline.apply_review(state, {}, answers)
    res = pipeline.finalize(job, state, ScoringConfig(marks_correct=1.5, marks_wrong=0))
    s = res["summary"]
    assert (s["correct"], s["wrong"], s["unanswered"], s["marks"], s["cutoff_count"]) == (119, 81, 0, 178.5, 119)


def test_reordered_pdf(golden):
    job, _ = golden
    reader = PdfReader(str(job / "out" / "reordered_response.pdf"))
    assert len(reader.pages) > 1
    seen = []
    for page in reader.pages[1:]:
        seen += re.findall(r"(?m)^\s*Q\.(\d+)\s*$", page.extract_text())
    assert sorted(map(int, seen)) == list(range(1, 201))
