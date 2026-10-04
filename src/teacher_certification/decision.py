"""认证决定：结论、差距与复评引用。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .framework import Dimension


class DecisionOutcome(Enum):
    PASS = "通过"
    CONDITIONAL_PASS = "附条件通过"
    REASSESS_AFTER_REMEDIATION = "补强后复评"
    FAIL = "不通过"


@dataclass(frozen=True)
class CertificationDecision:
    """一次认证决定；复评决定必须引用原决定与新增证据。"""

    decision_id: str
    case_id: str
    candidate_id: str
    framework_id: str
    framework_revision: int
    outcome: DecisionOutcome
    dimension_scores: dict[Dimension, float]
    dimension_gaps: dict[Dimension, float]
    decided_on: date
    rationale: str = ""
    supersedes: str | None = None
    new_evidence_ids: tuple[str, ...] = ()
