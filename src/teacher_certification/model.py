"""教师三维能力认证的领域模型。

三维能力：语言能力、教学技能、跨文化素养。
所有实体为不可变数据类，状态迁移由服务层以 replace 方式写回，
便于持久化与并发控制。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


class Dimension(enum.Enum):
    """三维能力维度。"""

    LANGUAGE = "语言能力"
    TEACHING = "教学技能"
    INTERCULTURAL = "跨文化素养"


class FrameworkStatus(enum.Enum):
    DRAFT = "草稿"
    PUBLISHED = "已发布"
    RETIRED = "已停用"


@dataclass(frozen=True)
class Indicator:
    """框架下的能力指标，归属于某一维度。"""

    indicator_id: str
    dimension: Dimension
    name: str
    weight: float = 1.0
    pass_score: float = 3.0


@dataclass(frozen=True)
class FrameworkVersion:
    """带版本与有效期的能力框架。过期框架不能签发新结论。"""

    framework_id: str
    version: int
    title: str
    indicators: tuple[Indicator, ...]
    dimension_thresholds: dict[Dimension, float]
    effective_from: date
    effective_to: Optional[date]  # None 表示长期有效
    status: FrameworkStatus = FrameworkStatus.DRAFT

    def is_effective_on(self, day: date) -> bool:
        if self.status is not FrameworkStatus.PUBLISHED:
            return False
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to

    def indicator(self, indicator_id: str) -> Optional[Indicator]:
        for ind in self.indicators:
            if ind.indicator_id == indicator_id:
                return ind
        return None


class EvidenceKind(enum.Enum):
    CLASSROOM_OBSERVATION = "课堂观察"
    ACADEMIC_ACHIEVEMENT = "学术成果"
    PRACTICE_FEEDBACK = "实践反馈"


@dataclass(frozen=True)
class EvidenceItem:
    """归入对应指标的证据材料。"""

    evidence_id: str
    case_id: str
    indicator_id: str
    kind: EvidenceKind
    summary: str
    recorded_on: date
    source: str


class TaskStatus(enum.Enum):
    PLANNED = "已排定"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


@dataclass(frozen=True)
class ObservationTask:
    """课堂观察任务，完成后产出课堂观察证据。"""

    task_id: str
    case_id: str
    assessor_id: str
    planned_on: date
    focus: str
    status: TaskStatus = TaskStatus.PLANNED
    completed_on: Optional[date] = None
    evidence_id: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    name: str


@dataclass(frozen=True)
class Assessor:
    """评委及其资质。"""

    assessor_id: str
    name: str
    qualifications: tuple[str, ...]
    dimensions: tuple[Dimension, ...]
    active: bool = True


class CaseStatus(enum.Enum):
    OPEN = "已立案"
    SCORING = "评分中"
    CALIBRATION_PENDING = "待校准"
    REVIEW_PENDING = "待复核"
    SCORES_CONFIRMED = "评分已确认"
    DECIDED = "已决定"
    REMEDIATION = "补强中"
    RE_REVIEW = "待复评"
    CLOSED = "已结案"


@dataclass(frozen=True)
class StatusEvent:
    status: CaseStatus
    on: date
    note: str = ""


@dataclass(frozen=True)
class DeviationFlag:
    """某一指标上评委评分的偏离情况。"""

    indicator_id: str
    scores: dict[str, float]  # assessor_id -> 评分
    spread: float


@dataclass(frozen=True)
class CertificationCase:
    """认证案件：候选人基于某一框架版本的认证全过程。"""

    case_id: str
    candidate_id: str
    framework_id: str
    framework_version: int
    opened_on: date
    status: CaseStatus
    panel: tuple[str, ...] = ()
    recused: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    remediation_plan_id: Optional[str] = None
    scoring_round: int = 0
    pending_deviation: tuple[DeviationFlag, ...] = ()
    history: tuple[StatusEvent, ...] = ()


@dataclass(frozen=True)
class ConflictDeclaration:
    """评委对某一案件的利益冲突申报。"""

    case_id: str
    assessor_id: str
    has_conflict: bool
    reason: str
    declared_on: date


class SheetKind(enum.Enum):
    ORIGINAL = "初评"
    CALIBRATION_REVISION = "校准修订"
    REVIEW_RESOLUTION = "复核裁定"


@dataclass(frozen=True)
class ScoreEntry:
    indicator_id: str
    score: float


@dataclass(frozen=True)
class ScoreSheet:
    """评委独立提交的评分表；校准修订与复核裁定以更高修订号或类别区分。"""

    sheet_id: str
    case_id: str
    assessor_id: str
    scoring_round: int
    entries: tuple[ScoreEntry, ...]
    submitted_on: date
    revision: int = 1
    kind: SheetKind = SheetKind.ORIGINAL


class CalibrationStatus(enum.Enum):
    OPEN = "校准中"
    CLOSED_RESOLVED = "校准通过"
    ESCALATED_TO_REVIEW = "转复核"


@dataclass(frozen=True)
class CalibrationSession:
    """评分偏离后发起的校准过程记录。"""

    session_id: str
    case_id: str
    moderator_id: str
    opened_on: date
    trigger: tuple[DeviationFlag, ...]
    status: CalibrationStatus = CalibrationStatus.OPEN
    closed_on: Optional[date] = None
    note: str = ""

    def status_on(self, day: date) -> CalibrationStatus:
        """查询日期当时的状态：尚未结束即为校准中。"""
        if self.closed_on is None or self.closed_on > day:
            return CalibrationStatus.OPEN
        return self.status


class DecisionOutcome(enum.Enum):
    PASS = "通过"
    CONDITIONAL_PASS = "附条件通过"
    REMEDIATION_THEN_REVIEW = "补强后复评"
    FAIL = "不通过"


@dataclass(frozen=True)
class Decision:
    """认证决定。复评决定必须引用原决定与新增证据。"""

    decision_id: str
    case_id: str
    outcome: DecisionOutcome
    rationale: str
    decided_on: date
    framework_id: str
    framework_version: int
    dimension_averages: dict[Dimension, float]
    supersedes_decision_id: Optional[str] = None
    new_evidence_ids: tuple[str, ...] = ()
    conditions: Optional[str] = None
    conditions_due: Optional[date] = None


class StepStatus(enum.Enum):
    PENDING = "待完成"
    SUBMITTED = "已提交"
    CONFIRMED = "导师已确认"


@dataclass(frozen=True)
class RemediationStep:
    """补强步骤：含前后依赖、完成期限与导师确认。"""

    step_id: str
    title: str
    depends_on: tuple[str, ...]
    deadline: date
    mentor_id: str
    status: StepStatus = StepStatus.PENDING
    submitted_on: Optional[date] = None
    confirmed_on: Optional[date] = None
    confirmed_by: Optional[str] = None


@dataclass(frozen=True)
class RemediationPlan:
    plan_id: str
    case_id: str
    created_on: date
    steps: tuple[RemediationStep, ...]

    def step(self, step_id: str) -> Optional[RemediationStep]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    @property
    def fulfilled(self) -> bool:
        return all(s.status is StepStatus.CONFIRMED for s in self.steps)


class CertificateStatus(enum.Enum):
    VALID = "有效"
    REVOKED = "已注销"


@dataclass(frozen=True)
class Certificate:
    """认证证书。同一候选人同一时间至多一张有效证书。"""

    certificate_id: str
    candidate_id: str
    case_id: str
    decision_id: str
    issued_on: date
    status: CertificateStatus = CertificateStatus.VALID
    revoked_on: Optional[date] = None
