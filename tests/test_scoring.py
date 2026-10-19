from app.scoring import ScoringConfig, score

ROWS = [
    {"key_answer": "A", "chosen": "A"},
    {"key_answer": "B", "chosen": "C"},
    {"key_answer": "C", "chosen": "E"},
    {"key_answer": "D", "chosen": None},
    {"key_answer": None, "chosen": "B"},
]


def _rows():
    return [dict(r) for r in ROWS]


def test_defaults():
    s = score(_rows(), ScoringConfig())
    assert (s["correct"], s["wrong"], s["chose_e"], s["unanswered"], s["key_unknown"]) == (1, 1, 1, 1, 1)
    assert s["marks"] == 1.5 and s["cutoff_count"] == 1


def test_negative_marking_and_e_as_wrong():
    s = score(_rows(), ScoringConfig(marks_correct=2, marks_wrong=0.5, e_policy="wrong"))
    assert s["wrong"] == 2 and s["chose_e"] == 1
    assert s["marks"] == 2 - 2 * 0.5


def test_unanswered_as_wrong():
    rows = _rows()
    s = score(rows, ScoringConfig(marks_wrong=1, unanswered_policy="wrong"))
    assert s["wrong"] == 2 and s["unanswered"] == 1
    assert rows[3]["result"] == "Wrong"
    assert s["marks"] == 1.5 - 2


def test_key_e_chosen_e_is_right():
    s = score([{"key_answer": "E", "chosen": "E"}], ScoringConfig())
    assert s["correct"] == 1 and s["chose_e"] == 0
