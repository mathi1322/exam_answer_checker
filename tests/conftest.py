import os
import shutil
from pathlib import Path

import pytest

from app import key_ocr
from app.response_parser import IonResponseSheet, ocr_questions
from tests.synth import make_fixture

FIXTURES = Path(__file__).parent / "fixtures"
THREADS = int(os.environ.get("OCR_THREADS", os.cpu_count() or 2))


@pytest.fixture(scope="session")
def synth(tmp_path_factory):
    """A 40-question synthetic key + response sheet (2 unanswered, 2 chose E)."""
    return make_fixture(tmp_path_factory.mktemp("synth"), n=40, seed=3, unanswered=2, choose_e=2)


@pytest.fixture(scope="session")
def synth_sheet(synth, tmp_path_factory):
    sheet = IonResponseSheet().parse(synth.response_pdf, tmp_path_factory.mktemp("resp_crops"))
    ocr_questions(sheet, THREADS)
    return sheet


@pytest.fixture(scope="session")
def synth_key(synth, tmp_path_factory):
    """(page OCR, segmentation, page image paths) for the synthetic key."""
    paths = key_ocr.render_pages(synth.key_pdf, tmp_path_factory.mktemp("key_pages"), threads=THREADS)
    pages = key_ocr.ocr_pages(paths, THREADS)
    return pages, key_ocr.segment(pages), paths


@pytest.fixture
def job_dir(tmp_path, synth):
    d = tmp_path / "job"
    d.mkdir()
    shutil.copy(synth.key_pdf, d / "key.pdf")
    shutil.copy(synth.response_pdf, d / "response.pdf")
    return d
