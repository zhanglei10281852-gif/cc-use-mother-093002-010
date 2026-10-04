"""认证案件：状态机与评分轮次。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class CaseStatus(Enum):
    OPEN = "立项"
    SCORING = "评分中"
    CALIBRATION = "待校准"
    REVIEW = "待复核"
    DECISION_READY = "待决定"
    DECIDED = "已决定"
    CLOSED = "已结案"


@dataclass
class CertificationCase:
    """一位候选人在某一框架版本下的认证案件。"""

    case_id: str
    candidate_id: str
    framework_id: str
    framework_revision: int
    assessor_ids: tuple[str, ...]
    opened_on: date
    status: CaseStatus = CaseStatus.OPEN
    round_no: int = 1
    decision_ids: list[str] = field(default_factory=list)
