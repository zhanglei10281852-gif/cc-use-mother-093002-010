"""观察任务与证据材料。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class EvidenceKind(Enum):
    CLASSROOM_OBSERVATION = "课堂观察"
    ACADEMIC = "学术成果"
    PRACTICE_FEEDBACK = "实践反馈"


class TaskStatus(Enum):
    PLANNED = "已计划"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


@dataclass
class ObservationTask:
    """一次课堂观察任务，完成后产出课堂观察证据。"""

    task_id: str
    case_id: str
    assessor_id: str
    indicator_codes: tuple[str, ...]
    planned_date: date
    status: TaskStatus = TaskStatus.PLANNED
    completed_date: date | None = None


@dataclass(frozen=True)
class Evidence:
    """归入框架指标的一份证据材料。"""

    evidence_id: str
    case_id: str
    kind: EvidenceKind
    indicator_codes: tuple[str, ...]
    framework_id: str
    framework_revision: int
    submitted_by: str
    submitted_on: date
    summary: str
    task_id: str | None = None
