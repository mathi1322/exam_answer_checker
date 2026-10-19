from app import pipeline
from app.config import Settings
from app.scoring import ScoringConfig


def _state():
    tick = {"answer": "A", "uncertain": False, "method": "image", "note": ""}
    return {
        "matches": [{"key_index": k, "resp_qnum": q, "score": 0.5, "margin": 0.2, "flagged": False, "reasons": []}
                    for k, q in ((1, 10), (2, 20), (3, 30))],
        "key": {"questions": [{"tick": dict(tick), "margin_number": None} for _ in range(3)], "margin_mismatches": []},
        "option_checks": {},
        "review": {"matches": {}, "answers": {}, "submitted": False},
    }


def test_review_swap_keeps_one_to_one():
    st = _state()
    pipeline.apply_review(st, {1: 30}, {})
    m = pipeline.current_mapping(st)
    assert m == {1: 30, 2: 20, 3: 10}
    assert sorted(m.values()) == [10, 20, 30]


def test_review_answer_override_and_revert():
    st = _state()
    pipeline.apply_review(st, {}, {2: "C"})
    assert pipeline.current_key_answer(st, 2) == ("C", "manual")
    pipeline.apply_review(st, {}, {2: "A"})  # back to the detected letter
    assert pipeline.current_key_answer(st, 2) == ("A", "image")


def test_ai_answers_are_flagged_for_review():
    st = _state()
    assert not pipeline.needs_review(st)
    st["key"]["questions"][1]["tick"].update(answer="B", method="ai", ai_answer="B")
    assert pipeline.needs_review(st)
    assert any("AI" in f for f in pipeline.flags_for(st, 2))
    assert pipeline.current_key_answer(st, 2) == ("B", "ai")  # still used for scoring


def test_end_to_end_synthetic(job_dir, synth):
    state = pipeline.process(job_dir, Settings(ocr_threads=4))
    truth = {q.key_index: q for q in synth.questions}
    mapping = pipeline.current_mapping(state)
    assert all(mapping[k] == truth[k].resp_qnum for k in truth)
    # Resolve anything uncertain the way a reviewer would.
    answers = {k: truth[k].answer for k in truth if pipeline.detected_key_answer(state, k) is None}
    pipeline.apply_review(state, {}, answers)
    res = pipeline.finalize(job_dir, state, ScoringConfig())
    expected_right = sum(q.chosen == q.answer for q in synth.questions)
    assert res["summary"]["correct"] == expected_right
    assert res["summary"]["unanswered"] == sum(q.chosen is None for q in synth.questions)
    for name in ("evaluation.csv", "results.json", "reordered_response.pdf"):
        assert (job_dir / "out" / name).stat().st_size > 0
