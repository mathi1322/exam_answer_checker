"""Plain dataclasses shared by the pipeline stages. All are JSON-serialisable via asdict()."""
from dataclasses import dataclass, field
from typing import Optional

OPTIONS = "ABCDE"


@dataclass
class BlockPart:
    """A vertical slice of a page, in PDF points measured from the page top."""
    page: int  # 0-based
    top: float
    bottom: float


@dataclass
class ResponseQuestion:
    qnum: int
    question_id: str = ""
    status: str = ""
    chosen: Optional[str] = None  # "A".."E" or None when not answered
    parts: list[BlockPart] = field(default_factory=list)  # whole block, for the reordered PDF
    image_page: int = -1
    image_bbox: tuple[float, float, float, float] = (0, 0, 0, 0)  # x0, top, x1, bottom (pt)
    crop_path: str = ""
    ocr_text: str = ""


@dataclass
class KeyPart:
    """A vertical slice of a rendered key page, in pixels at the render dpi."""
    page: int  # 0-based
    y0: int
    y1: int


@dataclass
class TickResult:
    answer: Optional[str] = None  # detected letter, None when uncertain
    uncertain: bool = True
    method: str = "image"  # image | ai | manual
    ink: dict[str, float] = field(default_factory=dict)
    labels_found: dict[str, list[int]] = field(default_factory=dict)  # letter -> [page, x0, y0, x1, y1]
    ai_answer: Optional[str] = None
    note: str = ""


@dataclass
class KeyQuestion:
    index: int  # 1-based, order in the answer key
    parts: list[KeyPart] = field(default_factory=list)
    text: str = ""
    margin_number: Optional[int] = None
    crop_path: str = ""
    tick: TickResult = field(default_factory=TickResult)


@dataclass
class MatchPair:
    key_index: int  # 1-based key question
    resp_qnum: int  # response Q.n
    score: float
    margin: float
    flagged: bool
    reasons: list[str] = field(default_factory=list)
