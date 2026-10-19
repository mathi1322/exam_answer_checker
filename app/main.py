"""FastAPI routes: upload, job status, review, results, downloads, delete."""
from __future__ import annotations

import json
import logging
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import pipeline
from .config import settings
from .jobs import DONE, PROCESSING, QUEUED, REVIEW, JobStore
from .scoring import ScoringConfig
from .validate import ValidationError, check_pdf, check_response_format

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE = Path(__file__).parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.mark_interrupted()
    store.cleanup()
    store.start_cleanup_thread()
    yield


app = FastAPI(title="Exam Answer Checker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
store = JobStore(settings)

DOWNLOADS = {
    "evaluation.csv": ("evaluation.csv", "text/csv"),
    "results.json": ("results.json", "application/json"),
    "reordered_response.pdf": ("reordered_response.pdf", "application/pdf"),
}


def _page(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("retention_hours", settings.retention_hours)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _job_or_404(job_id: str) -> tuple[dict, Path]:
    job = store.get(job_id)
    d = store.dir(job_id)
    if job is None or d is None:
        raise HTTPException(404, "This job does not exist or has been deleted.")
    return job, d


def _scoring_from_form(marks_correct: float, marks_wrong: float, e_policy: str, unanswered_policy: str) -> ScoringConfig:
    if marks_correct <= 0 or marks_wrong < 0:
        raise ValidationError("Marks per correct answer must be positive, and negative marks cannot be below zero.")
    return ScoringConfig(marks_correct=marks_correct, marks_wrong=marks_wrong,
                         e_policy="wrong" if e_policy == "wrong" else "separate",
                         unanswered_policy="wrong" if unanswered_policy == "wrong" else "separate")


async def _save_upload(upload: UploadFile, dest: Path, label: str) -> None:
    """Stream to disk, stopping as soon as the file is over the size limit."""
    size = 0
    with open(dest, "wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_file_mb * 1024 * 1024:
                raise ValidationError(f"The {label} is larger than the {settings.max_file_mb:g} MB limit.")
            fh.write(chunk)


def _check_uploads(d: Path, key_name: str, response_name: str) -> None:
    """PDF parsing is blocking, so the async upload route runs this in a worker thread."""
    check_pdf(d / "key.pdf", "answer key", key_name, settings.max_file_mb, settings.max_pages)
    check_pdf(d / "response.pdf", "response sheet", response_name, settings.max_file_mb, settings.max_pages)
    check_response_format(d / "response.pdf")


# ---------------------------------------------------------------- upload

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _page(request, "index.html", cfg=settings, defaults=ScoringConfig(settings.marks_correct, settings.marks_wrong))


@app.post("/upload")
async def upload(request: Request,
                 answer_key: UploadFile | None = File(None),
                 response_sheet: UploadFile | None = File(None),
                 marks_correct: float = Form(1.5), marks_wrong: float = Form(0.0),
                 e_policy: str = Form("separate"), unanswered_policy: str = Form("separate"),
                 use_ai: str | None = Form(None)):
    def fail(msg: str):
        return _page(request, "index.html", 400, cfg=settings, error=msg,
                     defaults=ScoringConfig(marks_correct, marks_wrong, e_policy, unanswered_policy))

    if not answer_key or not answer_key.filename:
        return fail("Please choose the answer key PDF.")
    if not response_sheet or not response_sheet.filename:
        return fail("Please choose the response sheet PDF.")
    try:
        scoring = _scoring_from_form(marks_correct, marks_wrong, e_policy, unanswered_policy)
    except ValidationError as exc:
        return fail(exc.message)

    job_id = store.new({"scoring": scoring.to_dict(), "use_ai": bool(use_ai) and settings.ai_available})
    d = store.dir(job_id)
    try:
        await _save_upload(answer_key, d / "key.pdf", "answer key")
        await _save_upload(response_sheet, d / "response.pdf", "response sheet")
        await run_in_threadpool(_check_uploads, d, answer_key.filename, response_sheet.filename)
    except ValidationError as exc:
        store.delete(job_id)
        return fail(exc.message)
    store.submit(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# ---------------------------------------------------------------- status

@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job, _ = _job_or_404(job_id)
    if job["status"] == REVIEW:
        return RedirectResponse(f"/jobs/{job_id}/review", status_code=303)
    if job["status"] == DONE:
        return RedirectResponse(f"/jobs/{job_id}/results", status_code=303)
    return _page(request, "status.html", job=job)


@app.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    job, _ = _job_or_404(job_id)
    return JSONResponse({k: job.get(k) for k in ("status", "stage", "progress", "message")})


# ---------------------------------------------------------------- review

def _resp_label(q: dict) -> str:
    words = " ".join(q["ocr_text"].split())[:70]
    return f'Q.{q["qnum"]} — {words}'


@app.get("/jobs/{job_id}/review", response_class=HTMLResponse)
def review_page(request: Request, job_id: str):
    job, d = _job_or_404(job_id)
    if job["status"] not in (REVIEW, DONE):
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    state = pipeline.load_state(d)
    S = np.load(d / "similarity.npy")
    resp_qs = state["response"]["questions"]
    resp = {q["qnum"]: q for q in resp_qs}
    mapping = pipeline.current_mapping(state)
    items = []
    for pair in state["matches"]:
        k = pair["key_index"]
        q = mapping[k]
        letter, method = pipeline.current_key_answer(state, k)
        tick = state["key"]["questions"][k - 1]["tick"]
        top = np.argsort(-S[k - 1])[:4]
        items.append({
            "k": k, "q": q, "label": _resp_label(resp[q]), "chosen": resp[q]["chosen"], "status": resp[q]["status"],
            "question_id": resp[q]["question_id"],
            "answer": letter, "method": method, "detected": pipeline.detected_key_answer(state, k),
            "tick_note": tick["note"], "ai_answer": tick.get("ai_answer"),
            "flags": pipeline.flags_for(state, k),
            "score": float(S[k - 1, [x["qnum"] for x in resp_qs].index(q)]),
            "alternatives": [(resp_qs[j]["qnum"], float(S[k - 1, j])) for j in top if resp_qs[j]["qnum"] != q][:3],
        })
    n_flagged = sum(1 for it in items if it["flags"])
    return _page(request, "review.html", job=job, items=items, n_flagged=n_flagged, state=state,
                 resp_labels=[_resp_label(q) for q in resp_qs])


@app.post("/jobs/{job_id}/review")
async def review_submit(request: Request, job_id: str):
    job, d = _job_or_404(job_id)
    if job["status"] not in (REVIEW, DONE):
        raise HTTPException(409, "This job is not ready for review.")
    form = await request.form()
    await run_in_threadpool(_apply_review_form, job, d, dict(form.items()))
    store.update(job_id, status=DONE, stage="Finished")
    return RedirectResponse(f"/jobs/{job_id}/results", status_code=303)


def _apply_review_form(job: dict, d: Path, form: dict) -> None:
    """Apply the review and rebuild the outputs (blocking; run in a worker thread)."""
    state = pipeline.load_state(d)
    valid_k = range(1, len(state["matches"]) + 1)
    valid_q = {q["qnum"] for q in state["response"]["questions"]}
    matches, answers = {}, {}
    for key, value in form.items():
        if m := re.fullmatch(r"match_(\d+)", key):
            k = int(m.group(1))
            if k in valid_k and (n := re.search(r"\d+", str(value))) and int(n.group()) in valid_q:
                matches[k] = int(n.group())
        elif m := re.fullmatch(r"answer_(\d+)", key):
            k = int(m.group(1))
            if k in valid_k:
                answers[k] = str(value) if str(value) in ("A", "B", "C", "D", "E") else None
    pipeline.apply_review(state, matches, answers)
    pipeline.save_state(d, state)
    pipeline.finalize(d, state, ScoringConfig(**job["options"]["scoring"]))


# ---------------------------------------------------------------- results

@app.get("/jobs/{job_id}/results", response_class=HTMLResponse)
def results_page(request: Request, job_id: str):
    job, d = _job_or_404(job_id)
    if job["status"] != DONE:
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    data = json.loads((d / "out" / "results.json").read_text())
    return _page(request, "results.html", job=job, data=data, summary=data["summary"])


@app.post("/jobs/{job_id}/rescore")
def rescore(job_id: str, marks_correct: float = Form(...), marks_wrong: float = Form(0.0),
            e_policy: str = Form("separate"), unanswered_policy: str = Form("separate")):
    job, d = _job_or_404(job_id)
    if job["status"] != DONE:
        raise HTTPException(409, "Results are not ready yet.")
    try:
        scoring = _scoring_from_form(marks_correct, marks_wrong, e_policy, unanswered_policy)
    except ValidationError as exc:
        raise HTTPException(400, exc.message)
    pipeline.finalize(d, pipeline.load_state(d), scoring)
    store.update(job_id, options={**job["options"], "scoring": scoring.to_dict()})
    return RedirectResponse(f"/jobs/{job_id}/results", status_code=303)


@app.get("/jobs/{job_id}/download/{name}")
def download(job_id: str, name: str):
    _, d = _job_or_404(job_id)
    if name not in DOWNLOADS or not (d / "out" / name).exists():
        raise HTTPException(404, "File not found.")
    filename, media = DOWNLOADS[name]
    return FileResponse(d / "out" / name, media_type=media, filename=filename)


@app.get("/jobs/{job_id}/crop/{kind}/{n}")
def crop(job_id: str, kind: str, n: int):
    _, d = _job_or_404(job_id)
    path = {"key": d / "crops" / f"key_{n:03d}.jpg", "resp": d / "crops" / f"resp_{n:03d}.png"}.get(kind)
    if path is None or not path.exists():
        raise HTTPException(404, "Image not found.")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@app.post("/jobs/{job_id}/delete")
def delete_job(request: Request, job_id: str):
    job = store.get(job_id)
    if job and job["status"] in (QUEUED, PROCESSING):
        raise HTTPException(409, "Please wait for processing to finish before deleting.")
    store.delete(job_id)
    return _page(request, "deleted.html")


@app.get("/healthz")
def healthz():
    ok = shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None
    return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

