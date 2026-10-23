"""Upload validation. Every failure raises ValidationError with a message fit for the user."""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .response_parser import ResponseSheetFormat, detect_format


class ValidationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def check_pdf(path: Path, label: str, original_name: str, max_mb: float, max_pages: int,
              max_page_inches: float = 20.0) -> int:
    """Basic file checks. Returns the page count.

    The page size limit matters because pdftoppm renders every page at RENDER_DPI: one huge page would exhaust
    memory or disk long before the page-count limit is reached.
    """
    if not path.exists() or path.stat().st_size == 0:
        raise ValidationError(f"Please choose a file for the {label}.")
    if not original_name.lower().endswith(".pdf"):
        raise ValidationError(f"The {label} must be a PDF file (.pdf). “{original_name}” is not.")
    if path.stat().st_size > max_mb * 1024 * 1024:
        raise ValidationError(f"The {label} is larger than the {max_mb:g} MB limit.")
    with open(path, "rb") as fh:
        head = fh.read(1024)
    if not head.lstrip(b"\x00\t\n\r ").startswith(b"%PDF"):
        raise ValidationError(f"The {label} is not a real PDF file. It may have been renamed from another format.")
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ValidationError(f"The {label} is password-protected. Please upload an unlocked copy.")
        n = len(reader.pages)
        if n <= max_pages:
            limit = max_page_inches * 72
            for i, page in enumerate(reader.pages, 1):
                box = page.mediabox
                unit = float(page.user_unit or 1)
                if max(abs(float(box.width)), abs(float(box.height))) * unit > limit:
                    raise ValidationError(f"Page {i} of the {label} is larger than {max_page_inches:g} inches. "
                                          f"Please upload a normal-sized (A4 or similar) document.")
    except ValidationError:
        raise
    except (PdfReadError, Exception):
        raise ValidationError(f"The {label} could not be read. The file may be damaged.")
    if n < 1:
        raise ValidationError(f"The {label} has no pages.")
    if n > max_pages:
        raise ValidationError(f"The {label} has {n} pages, more than the {max_pages}-page limit.")
    return n


def check_response_format(path: Path) -> ResponseSheetFormat:
    fmt = detect_format(path)
    if fmt is None:
        raise ValidationError(
            "The response sheet is not in a supported format. Expected a digital response sheet with "
            "“Q.1”, “Q.2”, … labels and “Question ID” / “Chosen Option” fields (for example a TNPSC iON sheet). "
            "Check that the two files are not swapped.")
    return fmt


def check_numbering(dups: list[int], missing: list[int], n_found: int) -> None:
    if n_found == 0:
        raise ValidationError("No questions (Q.1, Q.2, …) were found in the response sheet.")
    problems = []
    if dups:
        problems.append("duplicated: " + ", ".join(f"Q.{n}" for n in dups[:10]))
    if missing:
        problems.append("missing: " + ", ".join(f"Q.{n}" for n in missing[:10]) + (" …" if len(missing) > 10 else ""))
    if problems:
        raise ValidationError("The response sheet's question numbers are not complete (" + "; ".join(problems) + ").")


def check_counts(n_response: int, n_key: int) -> None:
    if n_key == 0:
        raise ValidationError("No question blocks could be detected in the answer key. Is it the right file?")
    if n_response != n_key:
        raise ValidationError(
            f"The question counts do not match: the response sheet has {n_response} questions but "
            f"{n_key} questions were detected in the answer key. Check that both files are for the same paper "
            f"and that the answer key is complete.")


def subject_warning(subject: str, cover_text: str) -> str | None:
    words = [w for w in re.findall(r"[a-z]{4,}", subject.lower()) if w not in ("paper", "test", "with")]
    if not words or not cover_text:
        return None
    cover = cover_text.lower()
    found = sum(1 for w in words if w in cover)
    if found / len(words) < 0.5:
        return ("The subject on the response sheet was not found on the answer key's cover page. "
                "Please check that both files are for the same paper.")
    return None
