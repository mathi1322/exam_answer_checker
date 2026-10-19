"""Match response-sheet questions to answer-key questions by their OCR'd text.

Character n-gram TF-IDF copes with noisy OCR (maths especially); the
Hungarian algorithm gives a one-to-one assignment.
"""
from __future__ import annotations

import re

import numpy as np
from scipy.optimize import linear_sum_assignment
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import MatchPair

ANSWER_NOT_KNOWN_RE = re.compile(r"answer\s*not\s*kn\w*", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]+")
VOWELS = set("aeiou")


def normalise(text: str) -> str:
    """Lowercase alphanumeric tokens, dropping vowel-less junk (mostly Tamil OCR noise)."""
    text = ANSWER_NOT_KNOWN_RE.sub(" ", text or "")
    toks = [t for t in TOKEN_RE.findall(text.lower()) if t.isdigit() or VOWELS & set(t)]
    return " ".join(toks)


def similarity_matrix(key_texts: list[str], resp_texts: list[str]) -> np.ndarray:
    """Cosine similarity, rows = key questions, columns = response questions."""
    k = [normalise(t) for t in key_texts]
    r = [normalise(t) for t in resp_texts]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
    vec.fit(k + r)
    return cosine_similarity(vec.transform(k), vec.transform(r))


def assign(S: np.ndarray) -> np.ndarray:
    """col[i] = response column assigned to key row i (one-to-one, maximum total similarity)."""
    rows, cols = linear_sum_assignment(-S)
    out = np.full(S.shape[0], -1, dtype=int)
    out[rows] = cols
    return out


def match(key_texts: list[str], resp_texts: list[str], resp_qnums: list[int],
          min_score: float = 0.35, min_margin: float = 0.08) -> tuple[list[MatchPair], np.ndarray]:
    S = similarity_matrix(key_texts, resp_texts)
    cols = assign(S)
    pairs = []
    for i, j in enumerate(cols):
        if j < 0:
            continue
        score = float(S[i, j])
        others = np.delete(S[i], j)
        margin = score - float(others.max()) if others.size else score
        reasons = []
        if score < min_score:
            reasons.append(f"low score {score:.2f}")
        if margin < min_margin:
            reasons.append(f"small margin {margin:.2f}")
        if int(S[i].argmax()) != j:
            reasons.append(f"best text match is Q.{resp_qnums[int(S[i].argmax())]}")
        pairs.append(MatchPair(i + 1, resp_qnums[j], round(score, 4), round(margin, 4), bool(reasons), reasons))
    return pairs, S
