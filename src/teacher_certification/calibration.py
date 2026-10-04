"""评分校准与外部复核。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class CalibrationStatus(Enum):
    OPEN = "校准中"
    RESOLVED = "已校准"
    ESCALATED = "已转复核"


class ReviewStatus(Enum):
    OPEN = "复核中"
    RESOLVED = "已复核"


@dataclass(frozen=True)
class ScoreRevision:
    """校准环节中评委对某一偏离指标的修订分。"""

    assessor_id: str
    indicator_code: str
    new_value: float
    revised_on: date


@dataclass
class CalibrationSession:
    """针对一轮评分中偏离指标的校准会话。"""

    session_id: str
    case_id: str
    round_no: int
    indicator_codes: tuple[str, ...]
    opened_on: date
    status: CalibrationStatus = CalibrationStatus.OPEN
    revisions: list[ScoreRevision] = field(default_factory=list)
    resolution_note: str = ""
    closed_on: date | None = None


@dataclass
class ReviewRequest:
    """校准仍无法收敛时发起的外部复核，由未参与评分的评委裁定。"""

    review_id: str
    case_id: str
    round_no: int
    session_id: str
    indicator_codes: tuple[str, ...]
    opened_on: date
    status: ReviewStatus = ReviewStatus.OPEN
    reviewer_id: str | None = None
    final_scores: dict[str, float] = field(default_factory=dict)
    note: str = ""
    resolved_on: date | None = None
