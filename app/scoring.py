"""Score the candidate's choices against the (translated) key answers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

RIGHT, WRONG, CHOSE_E, UNANSWERED, KEY_UNKNOWN = "Right", "Wrong", "Answer not known", "Unanswered", "Key unknown"


@dataclass
class ScoringConfig:
    marks_correct: float = 1.5
    marks_wrong: float = 0.0  # marks deducted per wrong answer (positive number)
    e_policy: str = "separate"  # separate | wrong  ("Answer not known" choices)
    unanswered_policy: str = "separate"  # separate | wrong

    def to_dict(self) -> dict:
        return asdict(self)


def classify(key: Optional[str], chosen: Optional[str], cfg: ScoringConfig) -> str:
    if not chosen:
        return WRONG if cfg.unanswered_policy == "wrong" and key else UNANSWERED
    if not key:
        return KEY_UNKNOWN
    if chosen == key:
        return RIGHT
    if chosen == "E":
        return WRONG if cfg.e_policy == "wrong" else CHOSE_E
    return WRONG


def score(rows: list[dict], cfg: ScoringConfig) -> dict:
    """rows need 'key_answer' (in response-sheet letters) and 'chosen'. Adds 'result' to each row."""
    counts = {RIGHT: 0, WRONG: 0, CHOSE_E: 0, UNANSWERED: 0, KEY_UNKNOWN: 0}
    for r in rows:
        r["result"] = classify(r.get("key_answer"), r.get("chosen"), cfg)
        counts[r["result"]] += 1
    marks = counts[RIGHT] * cfg.marks_correct - counts[WRONG] * cfg.marks_wrong
    return {
        "total": len(rows),
        "correct": counts[RIGHT],
        "wrong": counts[WRONG],
        "chose_e": sum(1 for r in rows if r.get("chosen") == "E" and r["result"] != RIGHT),
        "unanswered": sum(1 for r in rows if not r.get("chosen")),
        "key_unknown": counts[KEY_UNKNOWN],
        "marks": round(marks, 4),
        "max_marks": round(len(rows) * cfg.marks_correct, 4),
        "cutoff_count": counts[RIGHT],
        "config": cfg.to_dict(),
    }
