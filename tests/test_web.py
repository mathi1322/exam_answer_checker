import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.jobs import JobStore
from app.security import RateLimiter


@pytest.fixture
def cfg(tmp_path):
    return Settings(data_dir=tmp_path / "data", ocr_threads=4)


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.setattr(main, "settings", cfg)
    monkeypatch.setattr(main, "store", JobStore(cfg))
    monkeypatch.setattr(main, "upload_limiter", RateLimiter(cfg.uploads_per_hour))
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
    job_dir = main.store.root / job_id
    assert not (job_dir / "key.pdf").exists(), "the key is deleted once it has been read"
    assert (job_dir / "response.pdf").exists(), "the response sheet is kept for rebuilding the reordered PDF"
    with TestClient(main.app) as stranger:  # same link, another browser
        assert stranger.get(f"{job_url}/status").status_code == 404
        assert stranger.get(f"{job_url}/download/results.json").status_code == 404
        assert stranger.post(f"{job_url}/delete").status_code == 404

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


def test_job_needs_owner_cookie(client):
    job_id = main.store.new({"scoring": {}}, owner="a" * 43)
    assert client.get(f"/jobs/{job_id}/status").status_code == 404
    client.cookies.set("owner", "b" * 43)
    assert client.get(f"/jobs/{job_id}/status").status_code == 404
    client.cookies.set("owner", "a" * 43)
    assert client.get(f"/jobs/{job_id}/status").json()["status"] == "queued"
    assert main.store.get(job_id)["owner"] != "a" * 43  # only a hash is stored


def test_upload_sets_owner_cookie(client, synth, monkeypatch):
    monkeypatch.setattr(main.store, "submit", lambda job_id: None)  # no need to process it
    r = _upload(client, synth.key_pdf, synth.response_pdf)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("owner=") and "httponly" in cookie and "samesite=lax" in cookie


def test_https_owner_cookie_is_host_only(client, synth, monkeypatch):
    monkeypatch.setattr(main.store, "submit", lambda job_id: None)
    with TestClient(main.app, base_url="https://testserver") as c:
        r = _upload(c, synth.key_pdf, synth.response_pdf)
        assert r.status_code == 303
        cookie = r.headers["set-cookie"].lower()
        assert cookie.startswith("__host-owner=") and "secure" in cookie and "domain" not in cookie
        assert c.get(r.headers["location"] + "/status").status_code == 200
        # A plain "owner" cookie (e.g. planted from a subdomain) is not accepted over HTTPS.
        token = c.cookies.get("__Host-owner")
        c.cookies.clear()
        c.cookies.set("owner", token)
        assert c.get(r.headers["location"] + "/status").status_code == 404


def test_cross_site_posts_rejected(client):
    assert client.post("/upload", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/upload", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/jobs/x/delete", headers={"Origin": "null"}).status_code == 403
    # Same-origin browser posts get through to the route (which then complains about the missing files).
    r = client.post("/upload", headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 400


def test_security_headers(client):
    h = client.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["referrer-policy"] == "no-referrer" and h["x-content-type-options"] == "nosniff"
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404
    assert client.get("/jobs/missing/status").headers["cache-control"] == "no-store"


def test_upload_rate_limit(client, synth, monkeypatch):
    monkeypatch.setattr(main, "upload_limiter", RateLimiter(1))
    assert _upload(client, synth.response_pdf, synth.key_pdf).status_code == 400  # counted, then rejected as swapped
    r = _upload(client, synth.response_pdf, synth.key_pdf)
    assert r.status_code == 429 and "limit" in r.text


def test_upload_refused_when_busy_or_full(client, cfg, synth):
    cfg.max_pending_jobs = 0
    r = _upload(client, synth.key_pdf, synth.response_pdf)
    assert r.status_code == 503 and "busy" in r.text
    cfg.max_pending_jobs, cfg.max_disk_gb = 10, 0
    r = _upload(client, synth.key_pdf, synth.response_pdf)
    assert r.status_code == 503 and "out of space" in r.text
    assert not any(main.store.root.iterdir())


def test_oversized_request_rejected_before_parsing(client, cfg):
    cfg.max_file_mb = 0.1
    big = b"%PDF" + b"0" * (2 * 1024 * 1024)
    r = client.post("/upload", files={"answer_key": ("k.pdf", big, "application/pdf"),
                                      "response_sheet": ("r.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 413
