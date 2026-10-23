import pytest
from pypdf import PdfReader, PdfWriter

from app import pipeline
from app.config import Settings
from app.validate import ValidationError, check_numbering, check_pdf, check_response_format


def test_renamed_non_pdf(tmp_path):
    p = tmp_path / "photo.pdf"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2000)
    with pytest.raises(ValidationError, match="not a real PDF"):
        check_pdf(p, "answer key", "photo.pdf", 50, 300)


def test_wrong_extension(synth):
    with pytest.raises(ValidationError, match="must be a PDF"):
        check_pdf(synth.key_pdf, "answer key", "key.docx", 50, 300)


def test_encrypted(tmp_path, synth):
    w = PdfWriter(clone_from=str(synth.response_pdf))
    w.encrypt("secret", algorithm="RC4-128")
    p = tmp_path / "locked.pdf"
    w.write(p)
    with pytest.raises(ValidationError, match="password-protected"):
        check_pdf(p, "response sheet", "locked.pdf", 50, 300)


def test_huge_page_rejected(tmp_path):
    w = PdfWriter()
    w.add_blank_page(width=72 * 8.27, height=72 * 11.69)  # A4 is fine
    w.add_blank_page(width=72 * 100, height=72 * 100)
    p = tmp_path / "poster.pdf"
    w.write(p)
    with pytest.raises(ValidationError, match="Page 2 .* larger than 20 inches"):
        check_pdf(p, "answer key", "poster.pdf", 50, 300, max_page_inches=20)
    assert check_pdf(p, "answer key", "poster.pdf", 50, 300, max_page_inches=200) == 2


def test_oversized_and_too_many_pages(synth):
    with pytest.raises(ValidationError, match="MB limit"):
        check_pdf(synth.key_pdf, "answer key", "k.pdf", 0.01, 300)
    with pytest.raises(ValidationError, match="page limit"):
        check_pdf(synth.key_pdf, "answer key", "k.pdf", 50, 3)


def test_valid_files_pass(synth):
    assert check_pdf(synth.response_pdf, "response sheet", "r.pdf", 50, 300) >= 1
    check_response_format(synth.response_pdf)


def test_key_is_not_a_response_sheet(synth):
    with pytest.raises(ValidationError, match="not in a supported format"):
        check_response_format(synth.key_pdf)


def test_numbering_problems():
    with pytest.raises(ValidationError, match="duplicated: Q.3"):
        check_numbering([3], [], 10)
    with pytest.raises(ValidationError, match="missing: Q.7"):
        check_numbering([], [7], 10)


def test_mismatched_counts_report_both(job_dir, synth):
    # Drop one question page from the key.
    r = PdfReader(str(job_dir / "key.pdf"))
    w = PdfWriter()
    for i, page in enumerate(r.pages):
        if i != 2:
            w.add_page(page)
    w.write(job_dir / "key.pdf")
    with pytest.raises(ValidationError) as exc:
        pipeline.process(job_dir, Settings(ocr_threads=4))
    msg = exc.value.message
    assert f"response sheet has {len(synth.questions)} questions" in msg
    n_key = int(msg.split("but ")[1].split()[0])
    assert 0 < n_key < len(synth.questions)
