"""End-to-end processing of one job, plus applying the review and building outputs.

Job folder layout:
    key.pdf, response.pdf      uploads
    pages/                     answer-key renders (deleted after processing)
    crops/                     resp_NNN.png and key_NNN.jpg for the review screen
    state.json                 everything the review/results pages need
    similarity.npy             key x response text similarity
    out/                       evaluation.csv, results.json, reordered_response.pdf
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import key_ocr, matching, options, outputs, ticks
from .config import Settings
from .models import BlockPart
from .response_parser import detect_format, numbering_problems, ocr_questions
from .scoring import ScoringConfig, score
from .validate import ValidationError, check_counts, check_numbering, subject_warning

log = logging.getLogger("exam_checker")

Progress = Callable[[str, float], None]


def _noop(stage: str, frac: float) -> None:
    pass


def process(job_dir: Path, cfg: Settings, use_ai: bool = False, progress: Progress = _noop) -> dict:
    """Run every stage. Raises ValidationError for problems the user can fix."""
    crops = job_dir / "crops"
    pages_dir = job_dir / "pages"

    # 1. Response sheet: text layer + question image crops.
    progress("Reading the response sheet", 0.02)
    resp_pdf = job_dir / "response.pdf"
    fmt = detect_format(resp_pdf)
    if fmt is None:
        raise ValidationError("The response sheet is not in a supported format.")
    sheet = fmt.parse(resp_pdf, crops, dpi=cfg.render_dpi)
    dups, missing = numbering_problems(sheet)
    check_numbering(dups, missing, len(sheet.questions))
    sheet.questions.sort(key=lambda q: q.qnum)
    no_image = [q.qnum for q in sheet.questions if not q.crop_path]
    if len(no_image) > len(sheet.questions) // 2:
        raise ValidationError("Could not find the question images in the response sheet.")

    # 2. OCR the response questions.
    ocr_questions(sheet, cfg.ocr_threads, lambda i, n: progress(f"Reading response questions ({i}/{n})", 0.05 + 0.15 * i / n))

    # 3. Render and OCR the answer key.
    progress("Rendering the answer key", 0.2)
    page_paths = key_ocr.render_pages(job_dir / "key.pdf", pages_dir, cfg.render_dpi, cfg.ocr_threads)
    pages = key_ocr.ocr_pages(page_paths, cfg.ocr_threads,
                              lambda i, n: progress(f"Reading answer key pages ({i}/{n})", 0.22 + 0.6 * i / n))

    # 4. Split the key into questions.
    progress("Finding questions in the answer key", 0.83)
    seg = key_ocr.segment(pages, cfg.render_dpi)
    check_counts(len(sheet.questions), len(seg.questions))
    warnings = list(seg.warnings)
    if no_image:
        warnings.append(f"No question image found for {len(no_image)} response question(s): "
                        + ", ".join(f"Q.{n}" for n in no_image[:10]))
    if w := subject_warning(sheet.subject, key_ocr.cover_text(pages)):
        warnings.append(w)
    key_ocr.save_crops(seg.questions, page_paths, crops)

    # 5. Match questions by text.
    progress("Matching questions", 0.86)
    pairs, S = matching.match([q.text for q in seg.questions], [q.ocr_text for q in sheet.questions],
                              [q.qnum for q in sheet.questions], cfg.match_min_score, cfg.match_min_margin)
    np.save(job_dir / "similarity.npy", S)

    # 6. Tick detection.
    progress("Detecting ticks", 0.88)
    image_cache: dict[int, tuple] = {}
    for q in seg.questions:
        needed = {p.page for p in q.parts}
        for p in list(image_cache):
            if p not in needed:
                del image_cache[p]
        for p in needed:
            if p not in image_cache:
                image_cache[p] = ticks.load_page_image(page_paths[p])
        q.tick = ticks.detect_tick(q, pages, image_cache, cfg.tick_ratio, cfg.tick_runner_up_ratio)
    if use_ai:
        unsure = [q for q in seg.questions if q.tick.uncertain and q.crop_path]
        for i, q in enumerate(unsure, 1):
            progress(f"Asking Claude about unclear ticks ({i}/{len(unsure)})", 0.9 + 0.05 * i / len(unsure))
            try:
                q.tick.ai_answer = ticks.ai_tick(q.crop_path, cfg.anthropic_model)
            except Exception as exc:  # network/API problems must not fail the job
                log.warning("AI fallback failed: %s", type(exc).__name__)
                q.tick.note += "; AI fallback failed"
                continue
            if q.tick.ai_answer:
                q.tick.answer, q.tick.uncertain, q.tick.method = q.tick.ai_answer, False, "ai"

    # 7. Option order check.
    progress("Checking option order", 0.96)
    resp_by_q = sheet.by_qnum()
    option_checks = {}
    for pair in pairs:
        chk = options.compare(seg.questions[pair.key_index - 1].text, resp_by_q[pair.resp_qnum].ocr_text)
        option_checks[str(pair.key_index)] = asdict(chk)

    shutil.rmtree(pages_dir, ignore_errors=True)
    state = {
        "response": {"subject": sheet.subject, "page_sizes": sheet.page_sizes,
                     "questions": [asdict(q) for q in sheet.questions]},
        "key": {"n_pages": len(pages), "questions": [asdict(q) for q in seg.questions],
                "margin_mismatches": seg.margin_mismatches},
        "matches": [asdict(p) for p in pairs],
        "option_checks": option_checks,
        "warnings": warnings,
        "ai_used": use_ai,
        "review": {"matches": {}, "answers": {}, "submitted": False},
    }
    save_state(job_dir, state)
    progress("Done", 1.0)
    return state


# ---------------------------------------------------------------- state helpers

def save_state(job_dir: Path, state: dict) -> None:
    tmp = job_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(job_dir / "state.json")


def load_state(job_dir: Path) -> dict:
    return json.loads((job_dir / "state.json").read_text())


def current_mapping(state: dict) -> dict[int, int]:
    """key index -> response Q.n, after review overrides."""
    m = {p["key_index"]: p["resp_qnum"] for p in state["matches"]}
    m.update({int(k): v for k, v in state["review"]["matches"].items()})
    return m


def detected_key_answer(state: dict, k: int) -> Optional[str]:
    t = state["key"]["questions"][k - 1]["tick"]
    return None if t["uncertain"] else t["answer"]


def current_key_answer(state: dict, k: int) -> tuple[Optional[str], str]:
    """(letter, method) for key question k, after review overrides."""
    manual = state["review"]["answers"].get(str(k))
    if manual:
        return manual, "manual"
    return detected_key_answer(state, k), state["key"]["questions"][k - 1]["tick"]["method"]


def flags_for(state: dict, k: int) -> list[str]:
    reasons = []
    pair = state["matches"][k - 1]
    if pair["flagged"]:
        reasons += [f"Match: {r}" for r in pair["reasons"]]
    tick = state["key"]["questions"][k - 1]["tick"]
    if tick["uncertain"]:
        reasons.append(f"Tick unclear ({tick['note']})")
    elif tick["method"] == "ai":  # AI answers are used for scoring, but a person must confirm them
        reasons.append(f"Tick read by AI as {tick['answer']}: please confirm")
    chk = state["option_checks"].get(str(k), {})
    if chk.get("status") in ("reordered", "suspect"):
        reasons.append(f"Option order: {chk['status']} ({chk['detail']})")
    if k in state["key"].get("margin_mismatches", []):
        reasons.append(f"Margin number reads {state['key']['questions'][k - 1]['margin_number']}")
    return reasons


def needs_review(state: dict) -> bool:
    return any(flags_for(state, k) for k in range(1, len(state["matches"]) + 1))


def apply_review(state: dict, match_choices: dict[int, int], answer_choices: dict[int, Optional[str]]) -> None:
    """Apply the user's corrections, keeping the mapping one-to-one by swapping."""
    mapping = current_mapping(state)
    owner = {v: k for k, v in mapping.items()}
    for k, new_q in match_choices.items():
        old_q = mapping[k]
        if new_q == old_q or new_q not in owner:
            continue
        other = owner[new_q]
        mapping[k], mapping[other] = new_q, old_q
        owner[new_q], owner[old_q] = k, other
    original = {p["key_index"]: p["resp_qnum"] for p in state["matches"]}
    state["review"]["matches"] = {str(k): v for k, v in mapping.items() if original[k] != v}
    for k, letter in answer_choices.items():
        if letter and letter != detected_key_answer(state, k):
            state["review"]["answers"][str(k)] = letter
        else:
            state["review"]["answers"].pop(str(k), None)
    state["review"]["submitted"] = True


def build_rows(state: dict, S: Optional[np.ndarray] = None) -> list[dict]:
    mapping = current_mapping(state)
    resp = {q["qnum"]: q for q in state["response"]["questions"]}
    qnum_index = {q["qnum"]: i for i, q in enumerate(state["response"]["questions"])}
    rows = []
    for pair in state["matches"]:
        k = pair["key_index"]
        q = mapping[k]
        key_letter, method = current_key_answer(state, k)
        chk = options.OptionCheck(**state["option_checks"].get(str(k), {}))
        if q != pair["resp_qnum"]:  # re-matched by hand: the option check no longer applies
            chk = options.OptionCheck()
        rows.append({
            "key_index": k,
            "resp_qnum": q,
            "question_id": resp[q]["question_id"],
            "key_answer_in_key": key_letter,
            "key_answer": options.translate(key_letter, chk),
            "key_answer_method": method,
            "chosen": resp[q]["chosen"],
            "status": resp[q]["status"],
            "match_score": float(S[k - 1, qnum_index[q]]) if S is not None else pair["score"],
            "flagged": bool(flags_for(state, k)),
            "flags": flags_for(state, k),
            "match_manual": q != pair["resp_qnum"],
        })
    return rows


def finalize(job_dir: Path, state: dict, scoring: ScoringConfig) -> dict:
    """Score and write every output file. Returns the summary."""
    S_path = job_dir / "similarity.npy"
    rows = build_rows(state, np.load(S_path) if S_path.exists() else None)
    summary = score(rows, scoring)
    out = job_dir / "out"
    out.mkdir(exist_ok=True)
    outputs.write_csv(rows, out / "evaluation.csv")
    parts = {q["qnum"]: [BlockPart(**p) for p in q["parts"]] for q in state["response"]["questions"]}
    meta = {"subject": state["response"]["subject"]}
    n_pages = outputs.build_reordered_pdf(job_dir / "response.pdf", rows, parts, summary, meta,
                                          out / "reordered_response.pdf")
    key_qs = state["key"]["questions"]
    outputs.write_json({
        "summary": summary,
        "warnings": state["warnings"],
        "ai_fallback_used": state["ai_used"],
        "reordered_pdf_pages": n_pages,
        "questions": [{**r, "tick": {k: key_qs[r["key_index"] - 1]["tick"][k]
                                     for k in ("answer", "uncertain", "method", "ink", "ai_answer", "note")},
                       "option_check": state["option_checks"].get(str(r["key_index"])),
                       "key_margin_number": key_qs[r["key_index"] - 1]["margin_number"]}
                      for r in rows],
    }, out / "results.json")
    return {"summary": summary, "rows": rows}

