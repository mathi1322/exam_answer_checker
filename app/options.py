"""Check that the options appear in the same order in the key and the response sheet.

When both crops' option texts can be read, compare them letter by letter. A
confident permutation lets the key's letter be translated into the response
sheet's letter; anything doubtful is only flagged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

import numpy as np
from scipy.optimize import linear_sum_assignment

from .matching import normalise

LABEL_RE = re.compile(r"(?<![A-Za-z0-9])[\(\[]?([A-D])\s?[\.\)\]]\s+")
CONFIDENT_SIM = 0.6


@dataclass
class OptionCheck:
    status: str = "unchecked"  # same | reordered | suspect | unchecked
    mapping: dict[str, str] = field(default_factory=dict)  # key letter -> response letter (only when reordered)
    detail: str = ""


def split_options(text: str) -> dict[str, str]:
    """Option letter -> normalised option text, for the options whose extent is certain."""
    text = re.split(r"answer\s*not\s*kn", text or "", flags=re.I)[0]
    marks = list(LABEL_RE.finditer(text))
    # Statement text can contain "A." too: keep the last ascending run of labels.
    run: list[re.Match] = []
    for m in marks:
        if run and m.group(1) <= run[-1].group(1):
            run = []
        run.append(m)
    out: dict[str, str] = {}
    for i, m in enumerate(run):
        nxt = run[i + 1] if i + 1 < len(run) else None
        letter = m.group(1)
        # If the next label read is not the next letter (a ticked label is often
        # unreadable), this option's text may have swallowed the missing one.
        if nxt is not None and ord(nxt.group(1)) != ord(letter) + 1:
            continue
        if nxt is None and letter != "D":
            continue
        body = normalise(text[m.end(): nxt.start() if nxt else len(text)])
        if body:
            out[letter] = body
    return out if len(out) >= 2 else {}


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def compare(key_text: str, resp_text: str) -> OptionCheck:
    k, r = split_options(key_text), split_options(resp_text)
    common_k, common_r = sorted(k), sorted(r)
    if len(common_k) < 2 or len(common_r) < 2:
        return OptionCheck(detail="option texts could not be read in both documents")
    M = np.array([[_sim(k[a], r[b]) for b in common_r] for a in common_k])
    rows, cols = linear_sum_assignment(-M)
    best = {common_k[i]: common_r[j] for i, j in zip(rows, cols)}
    best_sims = {common_k[i]: M[i, j] for i, j in zip(rows, cols)}
    moved = {a: b for a, b in best.items() if a != b and best_sims[a] >= CONFIDENT_SIM
             and (b not in k or a not in r or M[common_k.index(a), common_r.index(a)] < 0.35)}
    if not moved:
        return OptionCheck("same")
    full = len(common_k) == 4 and len(common_r) == 4 and all(v >= CONFIDENT_SIM for v in best_sims.values())
    detail = ", ".join(f"key ({a}) looks like response ({b})" for a, b in sorted(moved.items()))
    if full:
        return OptionCheck("reordered", {**best, "E": "E"}, detail)
    return OptionCheck("suspect", {}, detail)


def translate(key_letter: Optional[str], check: OptionCheck) -> Optional[str]:
    if key_letter is None:
        return None
    return check.mapping.get(key_letter, key_letter) if check.status == "reordered" else key_letter
