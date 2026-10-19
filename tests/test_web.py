import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.jobs import JobStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = Settings(data_dir=tmp_path / "data", ocr_threads=4)
    monkeypatch.setattr(main, "settings", cfg)
    monkeypatch.setattr(main, "store", JobStore(cfg))
    with TestClient(main.app) as c:
        yield c


def _upload(client, key, resp, **form):
    with open(key, "rb") as k, open(resp, "rb") as r:
        return client.post("/upload", files={"answer_key": ("key.pdf", k, "application/pdf"),
                                             "response_sheet": ("resp.pdf", r, "application/pdf")},
                           data=form, follow_redirects=False)


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "Answer key" in r.text
    assert "Anthropic" not in r.text  # AI option hidden unless configured


def test_upload_rejects_swapped_files(client, synth):
    r = _upload(client, synth.response_pdf, synth.key_pdf)
    assert r.status_code == 400 and "not in a supported format" in r.text


def test_full_flow(client, synth):
    r = _upload(client, synth.key_pdf, synth.response_pdf, marks_correct="2", marks_wrong="0.5")
    assert r.status_code == 303
    job_url = r.headers["location"]
    job_id = job_url.rsplit("/", 1)[1]
    deadline = time.time() + 300
    while True:
        s = client.get(f"{job_url}/status").json()
        if s["status"] in ("review", "done", "error", "invalid"):
            break
        assert time.time() < deadline, "processing timed out"
        time.sleep(1)
    assert s["status"] in ("review", "done"), s

    r = client.get(f"{job_url}/review")
    assert r.status_code == 200 and "Answer key Q1" in r.text
    # Out-of-range question numbers are ignored, not a server error.
    r = client.post(f"{job_url}/review", data={"match_999": "Q.1", "answer_0": "A", "answer_999": "B"},
                    follow_redirects=False)
    assert r.status_code == 303
    # Swap the first two matches and confirm.
    r = client.post(f"{job_url}/review", data={"match_1": f"Q.{synth.questions[1].resp_qnum}"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"{job_url}/results")
    assert r.status_code == 200 and "cut-off count" in r.text
    data = client.get(f"{job_url}/download/results.json").json()
    m = {q["key_index"]: q["resp_qnum"] for q in data["questions"]}
    assert m[1] == synth.questions[1].resp_qnum and m[2] == synth.questions[0].resp_qnum
    assert data["summary"]["config"]["marks_correct"] == 2
    assert client.get(f"{job_url}/download/evaluation.csv").text.startswith("Answer key Q,")
    assert client.get(f"{job_url}/download/reordered_response.pdf").content[:4] == b"%PDF"
    assert client.get(f"{job_url}/crop/key/1").status_code == 200

    r = client.post(f"{job_url}/rescore", data={"marks_correct": "1", "marks_wrong": "0"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"{job_url}/download/results.json").json()["summary"]["config"]["marks_correct"] == 1

    assert client.post(f"{job_url}/delete").status_code == 200
    assert client.get(f"{job_url}/status").status_code == 404
    assert not (main.store.root / job_id).exists()


def test_path_traversal_job_id(client):
    assert client.get("/jobs/..%2F..%2Fetc/status").status_code == 404
