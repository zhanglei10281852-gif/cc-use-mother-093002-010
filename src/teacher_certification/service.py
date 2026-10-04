"""教师三维能力认证的应用服务。

贯穿流程：立案 → 证据归集 → 独立评分 → 偏离识别 → 校准/复核 →
认证决定 → 补强计划 → 复评 → 发证。
所有变更在单锁内完成并即时持久化，保证：
- 过期框架不能签发新结论；
- 并发操作不会产生两张有效证书；
- 服务重启后待校准案件保持原状态。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence

from .errors import (
    ConflictOfInterestError,
    DecisionError,
    DependencyError,
    DomainError,
    DuplicateCertificateError,
    FrameworkExpiredError,
    InvalidStateError,
    NotFoundError,
    QualificationError,
    StepOverdueError,
    ValidationError,
)
from .model import (
    Assessor,
    CalibrationSession,
    CalibrationStatus,
    Candidate,
    CaseStatus,
    Certificate,
    CertificateStatus,
    CertificationCase,
    ConflictDeclaration,
    Decision,
    DecisionOutcome,
    DeviationFlag,
    Dimension,
    EvidenceItem,
    EvidenceKind,
    FrameworkStatus,
    FrameworkVersion,
    Indicator,
    ObservationTask,
    RemediationPlan,
    RemediationStep,
    ScoreEntry,
    ScoreSheet,
    SheetKind,
    StatusEvent,
    StepStatus,
    TaskStatus,
)
from .repository import InMemoryRepository, Store

SCORE_MIN = 1.0
SCORE_MAX = 5.0
DEVIATION_SPREAD_THRESHOLD = 2.0
MIN_PANEL_SIZE = 2

_EVIDENCE_WINDOW = {CaseStatus.OPEN, CaseStatus.SCORING, CaseStatus.RE_REVIEW}
_CONFLICT_WINDOW = {CaseStatus.OPEN, CaseStatus.SCORING}


@dataclass(frozen=True)
class DimensionGap:
    """某一维度的均分与合格线差距（gap 为负即短板）。"""

    dimension: Dimension
    average: Optional[float]
    threshold: float
    gap: Optional[float]
    meets_threshold: bool


@dataclass(frozen=True)
class CertificationSnapshot:
    """评审秘书视角：某候选人在查询日期当时的认证全貌。"""

    candidate_id: str
    as_of: date
    case_id: Optional[str]
    case_status: Optional[CaseStatus]
    effective_decision: Optional[Decision]
    dimension_gaps: tuple[DimensionGap, ...]
    calibration_trail: tuple[CalibrationSession, ...]
    outstanding_steps: tuple[RemediationStep, ...]
    certificate: Optional[Certificate]


class CertificationService:
    def __init__(self, repository=None):
        self._lock = threading.RLock()
        self._repo = repository or InMemoryRepository()
        self.store: Store = self._repo.load()

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    @staticmethod
    def _framework_key(framework_id: str, version: int) -> str:
        return f"{framework_id}@v{version}"

    def _persist(self) -> None:
        self._repo.save(self.store)

    def _framework(self, framework_id: str, version: int) -> FrameworkVersion:
        key = self._framework_key(framework_id, version)
        fw = self.store.frameworks.get(key)
        if fw is None:
            raise NotFoundError(f"能力框架不存在: {key}")
        return fw

    def _case(self, case_id: str) -> CertificationCase:
        case = self.store.cases.get(case_id)
        if case is None:
            raise NotFoundError(f"认证案件不存在: {case_id}")
        return case

    def _case_framework(self, case: CertificationCase) -> FrameworkVersion:
        return self._framework(case.framework_id, case.framework_version)

    def _assessor(self, assessor_id: str) -> Assessor:
        assessor = self.store.assessors.get(assessor_id)
        if assessor is None:
            raise NotFoundError(f"评委不存在: {assessor_id}")
        return assessor

    def _save_case(self, case: CertificationCase) -> None:
        self.store.cases[case.case_id] = case

    @staticmethod
    def _transition(case: CertificationCase, status: CaseStatus, on: date, note: str = "") -> CertificationCase:
        return replace(
            case,
            status=status,
            history=case.history + (StatusEvent(status=status, on=on, note=note),),
        )

    def _require_status(self, case: CertificationCase, allowed: set[CaseStatus], action: str) -> None:
        if case.status not in allowed:
            names = "、".join(sorted(s.value for s in allowed))
            raise InvalidStateError(f"案件 {case.case_id} 当前为「{case.status.value}」，无法{action}（需要：{names}）")

    # ------------------------------------------------------------------
    # 能力框架管理（带版本）
    # ------------------------------------------------------------------
    def register_framework_version(
        self,
        framework_id: str,
        version: int,
        title: str,
        indicators: Sequence[Indicator],
        dimension_thresholds: dict[Dimension, float],
        effective_from: date,
        effective_to: Optional[date],
    ) -> FrameworkVersion:
        with self._lock:
            if not framework_id or not title:
                raise ValidationError("框架标识与名称不能为空")
            if version < 1:
                raise ValidationError("框架版本号必须不小于 1")
            key = self._framework_key(framework_id, version)
            if key in self.store.frameworks:
                raise ValidationError(f"框架版本已存在: {key}")
            indicators = tuple(indicators)
            if not indicators:
                raise ValidationError("框架必须包含能力指标")
            ids = [ind.indicator_id for ind in indicators]
            if len(set(ids)) != len(ids) or not all(ids):
                raise ValidationError("指标标识重复或为空")
            covered = {ind.dimension for ind in indicators}
            if covered != set(Dimension):
                raise ValidationError("框架指标必须覆盖语言能力、教学技能、跨文化素养三个维度")
            for ind in indicators:
                if not (SCORE_MIN <= ind.pass_score <= SCORE_MAX):
                    raise ValidationError(f"指标 {ind.indicator_id} 合格线超出评分区间")
                if ind.weight <= 0:
                    raise ValidationError(f"指标 {ind.indicator_id} 权重必须为正")
            if set(dimension_thresholds) != set(Dimension):
                raise ValidationError("必须为三个维度分别设定合格线")
            for dim, line in dimension_thresholds.items():
                if not (SCORE_MIN <= line <= SCORE_MAX):
                    raise ValidationError(f"维度 {dim.value} 合格线超出评分区间")
            if effective_to is not None and effective_to <= effective_from:
                raise ValidationError("框架失效日期必须晚于生效日期")
            fw = FrameworkVersion(
                framework_id=framework_id,
                version=version,
                title=title,
                indicators=indicators,
                dimension_thresholds=dict(dimension_thresholds),
                effective_from=effective_from,
                effective_to=effective_to,
            )
            self.store.frameworks[key] = fw
            self._persist()
            return fw

    def publish_framework(self, framework_id: str, version: int) -> FrameworkVersion:
        with self._lock:
            fw = self._framework(framework_id, version)
            if fw.status is not FrameworkStatus.DRAFT:
                raise InvalidStateError("只有草稿状态的框架可以发布")
            fw = replace(fw, status=FrameworkStatus.PUBLISHED)
            self.store.frameworks[self._framework_key(framework_id, version)] = fw
            self._persist()
            return fw

    def retire_framework(self, framework_id: str, version: int) -> FrameworkVersion:
        with self._lock:
            fw = self._framework(framework_id, version)
            if fw.status is not FrameworkStatus.PUBLISHED:
                raise InvalidStateError("只有已发布的框架可以停用")
            fw = replace(fw, status=FrameworkStatus.RETIRED)
            self.store.frameworks[self._framework_key(framework_id, version)] = fw
            self._persist()
            return fw

    # ------------------------------------------------------------------
    # 候选人与评委资质
    # ------------------------------------------------------------------
    def register_candidate(self, candidate_id: str, name: str) -> Candidate:
        with self._lock:
            if not candidate_id or not name:
                raise ValidationError("候选人标识与姓名不能为空")
            if candidate_id in self.store.candidates:
                raise ValidationError(f"候选人已存在: {candidate_id}")
            candidate = Candidate(candidate_id=candidate_id, name=name)
            self.store.candidates[candidate_id] = candidate
            self._persist()
            return candidate

    def register_assessor(
        self,
        assessor_id: str,
        name: str,
        qualifications: Sequence[str],
        dimensions: Sequence[Dimension],
    ) -> Assessor:
        with self._lock:
            if not assessor_id or not name:
                raise ValidationError("评委标识与姓名不能为空")
            if assessor_id in self.store.assessors:
                raise ValidationError(f"评委已存在: {assessor_id}")
            if not qualifications:
                raise QualificationError("评委必须持有至少一项资质")
            if not dimensions:
                raise QualificationError("评委必须登记可评维度")
            assessor = Assessor(
                assessor_id=assessor_id,
                name=name,
                qualifications=tuple(qualifications),
                dimensions=tuple(dict.fromkeys(dimensions)),
            )
            self.store.assessors[assessor_id] = assessor
            self._persist()
            return assessor

    def deactivate_assessor(self, assessor_id: str) -> Assessor:
        with self._lock:
            assessor = replace(self._assessor(assessor_id), active=False)
            self.store.assessors[assessor_id] = assessor
            self._persist()
            return assessor

    # ------------------------------------------------------------------
    # 案件、观察任务与证据
    # ------------------------------------------------------------------
    def open_case(
        self,
        case_id: str,
        candidate_id: str,
        framework_id: str,
        version: int,
        opened_on: date,
    ) -> CertificationCase:
        with self._lock:
            if candidate_id not in self.store.candidates:
                raise NotFoundError(f"候选人不存在: {candidate_id}")
            if case_id in self.store.cases:
                raise ValidationError(f"案件已存在: {case_id}")
            fw = self._framework(framework_id, version)
            if not fw.is_effective_on(opened_on):
                raise FrameworkExpiredError(f"框架 {framework_id} v{version} 在 {opened_on} 不可用于立案")
            for existing in self.store.cases.values():
                if existing.candidate_id != candidate_id:
                    continue
                if existing.status is CaseStatus.CLOSED:
                    continue
                if existing.status is CaseStatus.DECIDED:
                    last = self.store.decisions[existing.decision_ids[-1]].outcome
                    if last in (DecisionOutcome.PASS, DecisionOutcome.FAIL):
                        continue
                raise InvalidStateError(f"候选人已有进行中的案件 {existing.case_id}，不得重复立案")
            case = CertificationCase(
                case_id=case_id,
                candidate_id=candidate_id,
                framework_id=framework_id,
                framework_version=version,
                opened_on=opened_on,
                status=CaseStatus.OPEN,
                history=(StatusEvent(status=CaseStatus.OPEN, on=opened_on, note="立案"),),
            )
            self._save_case(case)
            self._persist()
            return case

    def assign_panel(self, case_id: str, assessor_ids: Sequence[str]) -> CertificationCase:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.OPEN}, "指派评审组")
            ids = tuple(assessor_ids)
            if len(set(ids)) != len(ids) or len(ids) < MIN_PANEL_SIZE:
                raise ValidationError(f"评审组至少需要 {MIN_PANEL_SIZE} 名不同评委")
            covered: set[Dimension] = set()
            for aid in ids:
                assessor = self._assessor(aid)
                if not assessor.active:
                    raise QualificationError(f"评委 {aid} 已停用，不能进入评审组")
                covered |= set(assessor.dimensions)
            if covered != set(Dimension):
                raise QualificationError("评审组的可评维度必须覆盖全部三个维度")
            case = replace(case, panel=ids)
            self._save_case(case)
            self._persist()
            return case

    def plan_observation_task(
        self,
        case_id: str,
        task_id: str,
        assessor_id: str,
        planned_on: date,
        focus: str,
    ) -> ObservationTask:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.OPEN, CaseStatus.SCORING}, "排定观察任务")
            if task_id in self.store.tasks:
                raise ValidationError(f"观察任务已存在: {task_id}")
            if assessor_id not in case.panel or assessor_id in case.recused:
                raise ValidationError(f"评委 {assessor_id} 不在本案评审组内")
            task = ObservationTask(
                task_id=task_id,
                case_id=case_id,
                assessor_id=assessor_id,
                planned_on=planned_on,
                focus=focus,
            )
            self.store.tasks[task_id] = task
            self._persist()
            return task

    def complete_observation_task(
        self,
        task_id: str,
        completed_on: date,
        indicator_id: str,
        summary: str,
        evidence_id: str,
    ) -> EvidenceItem:
        with self._lock:
            task = self.store.tasks.get(task_id)
            if task is None:
                raise NotFoundError(f"观察任务不存在: {task_id}")
            if task.status is not TaskStatus.PLANNED:
                raise InvalidStateError(f"观察任务 {task_id} 当前为「{task.status.value}」，无法完成登记")
            evidence = self._record_evidence_locked(
                case_id=task.case_id,
                evidence_id=evidence_id,
                indicator_id=indicator_id,
                kind=EvidenceKind.CLASSROOM_OBSERVATION,
                summary=summary,
                recorded_on=completed_on,
                source=f"观察任务 {task_id}",
            )
            self.store.tasks[task_id] = replace(
                task, status=TaskStatus.COMPLETED, completed_on=completed_on, evidence_id=evidence_id
            )
            self._persist()
            return evidence

    def record_evidence(
        self,
        case_id: str,
        evidence_id: str,
        indicator_id: str,
        kind: EvidenceKind,
        summary: str,
        recorded_on: date,
        source: str,
    ) -> EvidenceItem:
        with self._lock:
            evidence = self._record_evidence_locked(
                case_id, evidence_id, indicator_id, kind, summary, recorded_on, source
            )
            self._persist()
            return evidence

    def _record_evidence_locked(
        self,
        case_id: str,
        evidence_id: str,
        indicator_id: str,
        kind: EvidenceKind,
        summary: str,
        recorded_on: date,
        source: str,
    ) -> EvidenceItem:
        case = self._case(case_id)
        self._require_status(case, _EVIDENCE_WINDOW, "登记证据")
        fw = self._case_framework(case)
        if fw.indicator(indicator_id) is None:
            raise ValidationError(f"指标 {indicator_id} 不属于案件所用框架版本")
        if evidence_id in self.store.evidence:
            raise ValidationError(f"证据已存在: {evidence_id}")
        if not summary:
            raise ValidationError("证据摘要不能为空")
        evidence = EvidenceItem(
            evidence_id=evidence_id,
            case_id=case_id,
            indicator_id=indicator_id,
            kind=kind,
            summary=summary,
            recorded_on=recorded_on,
            source=source,
        )
        self.store.evidence[evidence_id] = evidence
        return evidence

    # ------------------------------------------------------------------
    # 利益冲突与独立评分
    # ------------------------------------------------------------------
    def declare_conflict(
        self,
        case_id: str,
        assessor_id: str,
        has_conflict: bool,
        reason: str,
        declared_on: date,
    ) -> ConflictDeclaration:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, _CONFLICT_WINDOW, "申报利益冲突")
            self._assessor(assessor_id)
            key = f"{case_id}:{assessor_id}"
            if key in self.store.declarations:
                raise ValidationError(f"评委 {assessor_id} 已就本案申报过利益冲突")
            if has_conflict and not reason:
                raise ValidationError("申报存在利益冲突时必须说明理由")
            declaration = ConflictDeclaration(
                case_id=case_id,
                assessor_id=assessor_id,
                has_conflict=has_conflict,
                reason=reason,
                declared_on=declared_on,
            )
            self.store.declarations[key] = declaration
            if has_conflict and assessor_id in case.panel and assessor_id not in case.recused:
                case = replace(case, recused=case.recused + (assessor_id,))
                self._save_case(case)
                self._maybe_finalize_scoring(case, declared_on)
            self._persist()
            return declaration

    def open_scoring(self, case_id: str, on: date) -> CertificationCase:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.OPEN, CaseStatus.RE_REVIEW}, "开启评分")
            active_panel = [a for a in case.panel if a not in case.recused]
            if len(active_panel) < MIN_PANEL_SIZE:
                raise ValidationError(f"评审组有效成员不足 {MIN_PANEL_SIZE} 人，无法开启评分")
            if not any(e.case_id == case_id for e in self.store.evidence.values()):
                raise ValidationError("尚无归入指标的证据，无法开启评分")
            case = replace(case, scoring_round=case.scoring_round + 1, pending_deviation=())
            case = self._transition(case, CaseStatus.SCORING, on, f"第 {case.scoring_round} 轮评分")
            self._save_case(case)
            self._persist()
            return case

    def submit_score_sheet(
        self,
        case_id: str,
        sheet_id: str,
        assessor_id: str,
        entries: Sequence[ScoreEntry],
        submitted_on: date,
    ) -> ScoreSheet:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.SCORING}, "提交评分表")
            self._check_scorer(case, assessor_id)
            if any(
                s.case_id == case_id
                and s.assessor_id == assessor_id
                and s.scoring_round == case.scoring_round
                for s in self.store.sheets.values()
            ):
                raise ValidationError(f"评委 {assessor_id} 本轮已提交评分表")
            sheet = ScoreSheet(
                sheet_id=sheet_id,
                case_id=case_id,
                assessor_id=assessor_id,
                scoring_round=case.scoring_round,
                entries=self._validate_entries(case, entries),
                submitted_on=submitted_on,
            )
            self._store_sheet(sheet)
            self._maybe_finalize_scoring(self._case(case_id), submitted_on)
            self._persist()
            return sheet

    def _store_sheet(self, sheet: ScoreSheet) -> None:
        if sheet.sheet_id in self.store.sheets:
            raise ValidationError(f"评分表已存在: {sheet.sheet_id}")
        self.store.sheets[sheet.sheet_id] = sheet

    def _check_scorer(self, case: CertificationCase, assessor_id: str) -> None:
        if assessor_id not in case.panel:
            raise ValidationError(f"评委 {assessor_id} 不在本案评审组内")
        if assessor_id in case.recused:
            raise ConflictOfInterestError(f"评委 {assessor_id} 已因利益冲突回避，不能评分")
        if not self._assessor(assessor_id).active:
            raise QualificationError(f"评委 {assessor_id} 已停用，不能评分")

    def _validate_entries(self, case: CertificationCase, entries: Sequence[ScoreEntry]) -> tuple[ScoreEntry, ...]:
        fw = self._case_framework(case)
        expected = {ind.indicator_id for ind in fw.indicators}
        entries = tuple(entries)
        got = [e.indicator_id for e in entries]
        if set(got) != expected or len(got) != len(expected):
            raise ValidationError("评分表必须恰好覆盖框架全部指标")
        for e in entries:
            if not (SCORE_MIN <= e.score <= SCORE_MAX):
                raise ValidationError(f"指标 {e.indicator_id} 评分超出 [{SCORE_MIN}, {SCORE_MAX}] 区间")
        return entries

    def _maybe_finalize_scoring(self, case: CertificationCase, on: date) -> None:
        if case.status is not CaseStatus.SCORING:
            return
        active_panel = [a for a in case.panel if a not in case.recused]
        if not active_panel:
            return
        sheets = self.store.sheets.values()
        submitted = {
            s.assessor_id
            for s in sheets
            if s.case_id == case.case_id and s.scoring_round == case.scoring_round
        }
        if not all(a in submitted for a in active_panel):
            return
        flags = self._detect_deviation(case)
        if flags:
            case = replace(case, pending_deviation=flags)
            case = self._transition(case, CaseStatus.CALIBRATION_PENDING, on, "系统识别到评分偏离")
        else:
            case = self._transition(case, CaseStatus.SCORES_CONFIRMED, on, "评分无偏离")
        self._save_case(case)

    # ------------------------------------------------------------------
    # 评分偏离、校准与复核
    # ------------------------------------------------------------------
    def _round_sheets(
        self,
        case: CertificationCase,
        as_of: Optional[date] = None,
        scoring_round: Optional[int] = None,
    ) -> list[ScoreSheet]:
        """每位未回避评委在指定轮次的最新评分表（初评或校准修订）。"""
        recused = set(case.recused)
        if as_of is not None:
            recused = {
                d.assessor_id
                for d in self.store.declarations.values()
                if d.case_id == case.case_id and d.has_conflict and d.declared_on <= as_of
            }
        latest: dict[tuple[str, int], ScoreSheet] = {}
        for sheet in self.store.sheets.values():
            if sheet.case_id != case.case_id or sheet.kind is SheetKind.REVIEW_RESOLUTION:
                continue
            if as_of is not None and sheet.submitted_on > as_of:
                continue
            key = (sheet.assessor_id, sheet.scoring_round)
            current = latest.get(key)
            if current is None or (sheet.revision, sheet.submitted_on) > (current.revision, current.submitted_on):
                latest[key] = sheet
        if scoring_round is None:
            rounds = {r for _, r in latest}
            if not rounds:
                return []
            scoring_round = max(rounds) if as_of is not None else case.scoring_round
        return [
            sheet
            for (aid, rnd), sheet in latest.items()
            if rnd == scoring_round and aid not in recused
        ]

    def _detect_deviation(self, case: CertificationCase) -> tuple[DeviationFlag, ...]:
        fw = self._case_framework(case)
        sheets = self._round_sheets(case)
        flags: list[DeviationFlag] = []
        for ind in fw.indicators:
            scores: dict[str, float] = {}
            for sheet in sheets:
                for entry in sheet.entries:
                    if entry.indicator_id == ind.indicator_id:
                        scores[sheet.assessor_id] = entry.score
            if len(scores) >= 2:
                spread = max(scores.values()) - min(scores.values())
                if spread >= DEVIATION_SPREAD_THRESHOLD:
                    flags.append(DeviationFlag(ind.indicator_id, scores, spread))
        return tuple(flags)

    def open_calibration(self, case_id: str, session_id: str, moderator_id: str, opened_on: date) -> CalibrationSession:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.CALIBRATION_PENDING}, "发起校准")
            moderator = self._assessor(moderator_id)
            if not moderator.active or moderator_id in case.recused:
                raise QualificationError("校准主持人必须是未回避的在职评委")
            if any(
                s.case_id == case_id and s.status is CalibrationStatus.OPEN
                for s in self.store.calibrations.values()
            ):
                raise InvalidStateError("本案已有进行中的校准")
            if session_id in self.store.calibrations:
                raise ValidationError(f"校准会话已存在: {session_id}")
            session = CalibrationSession(
                session_id=session_id,
                case_id=case_id,
                moderator_id=moderator_id,
                opened_on=opened_on,
                trigger=case.pending_deviation,
            )
            self.store.calibrations[session_id] = session
            self._persist()
            return session

    def submit_calibration_revision(
        self,
        case_id: str,
        sheet_id: str,
        assessor_id: str,
        entries: Sequence[ScoreEntry],
        submitted_on: date,
    ) -> ScoreSheet:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.CALIBRATION_PENDING}, "提交校准修订")
            if not any(
                s.case_id == case_id and s.status is CalibrationStatus.OPEN
                for s in self.store.calibrations.values()
            ):
                raise InvalidStateError("校准会话尚未开启")
            self._check_scorer(case, assessor_id)
            current = [
                s
                for s in self.store.sheets.values()
                if s.case_id == case_id
                and s.assessor_id == assessor_id
                and s.scoring_round == case.scoring_round
            ]
            if not current:
                raise ValidationError(f"评委 {assessor_id} 本轮尚无初评表，无法修订")
            sheet = ScoreSheet(
                sheet_id=sheet_id,
                case_id=case_id,
                assessor_id=assessor_id,
                scoring_round=case.scoring_round,
                entries=self._validate_entries(case, entries),
                submitted_on=submitted_on,
                revision=max(s.revision for s in current) + 1,
                kind=SheetKind.CALIBRATION_REVISION,
            )
            self._store_sheet(sheet)
            self._persist()
            return sheet

    def close_calibration(self, case_id: str, session_id: str, closed_on: date, note: str = "") -> CalibrationSession:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.CALIBRATION_PENDING}, "结束校准")
            session = self.store.calibrations.get(session_id)
            if session is None or session.case_id != case_id:
                raise NotFoundError(f"校准会话不存在: {session_id}")
            if session.status is not CalibrationStatus.OPEN:
                raise InvalidStateError("校准会话已结束")
            flags = self._detect_deviation(case)
            if flags:
                session = replace(session, status=CalibrationStatus.ESCALATED_TO_REVIEW, closed_on=closed_on, note=note)
                case = replace(case, pending_deviation=flags)
                case = self._transition(case, CaseStatus.REVIEW_PENDING, closed_on, "校准后仍有偏离，转复核")
            else:
                session = replace(session, status=CalibrationStatus.CLOSED_RESOLVED, closed_on=closed_on, note=note)
                case = replace(case, pending_deviation=())
                case = self._transition(case, CaseStatus.SCORES_CONFIRMED, closed_on, "校准后偏离消除")
            self.store.calibrations[session_id] = session
            self._save_case(case)
            self._persist()
            return session

    def submit_review_resolution(
        self,
        case_id: str,
        sheet_id: str,
        reviewer_id: str,
        entries: Sequence[ScoreEntry],
        submitted_on: date,
    ) -> ScoreSheet:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.REVIEW_PENDING}, "提交复核裁定")
            reviewer = self._assessor(reviewer_id)
            if not reviewer.active or reviewer_id in case.recused:
                raise QualificationError("复核人必须是未回避的在职评委")
            declaration = self.store.declarations.get(f"{case_id}:{reviewer_id}")
            if declaration is not None and declaration.has_conflict:
                raise ConflictOfInterestError("复核人已申报利益冲突，不能裁定本案")
            if any(
                s.case_id == case_id
                and s.scoring_round == case.scoring_round
                and s.kind is SheetKind.REVIEW_RESOLUTION
                for s in self.store.sheets.values()
            ):
                raise InvalidStateError("本轮已有复核裁定")
            fw = self._case_framework(case)
            valid_ids = {ind.indicator_id for ind in fw.indicators}
            entries = tuple(entries)
            if not entries or any(e.indicator_id not in valid_ids for e in entries):
                raise ValidationError("复核裁定包含框架外指标")
            for e in entries:
                if not (SCORE_MIN <= e.score <= SCORE_MAX):
                    raise ValidationError(f"指标 {e.indicator_id} 裁定分超出评分区间")
            disputed = {f.indicator_id for f in case.pending_deviation}
            if not disputed <= {e.indicator_id for e in entries}:
                raise ValidationError("复核裁定必须覆盖全部争议指标")
            sheet = ScoreSheet(
                sheet_id=sheet_id,
                case_id=case_id,
                assessor_id=reviewer_id,
                scoring_round=case.scoring_round,
                entries=entries,
                submitted_on=submitted_on,
                kind=SheetKind.REVIEW_RESOLUTION,
            )
            self._store_sheet(sheet)
            case = replace(case, pending_deviation=())
            case = self._transition(case, CaseStatus.SCORES_CONFIRMED, submitted_on, "复核裁定生效")
            self._save_case(case)
            self._persist()
            return sheet

    # ------------------------------------------------------------------
    # 分数汇总
    # ------------------------------------------------------------------
    def _final_indicator_scores(
        self, case: CertificationCase, as_of: Optional[date] = None
    ) -> dict[str, float]:
        fw = self._case_framework(case)
        sheets = self._round_sheets(case, as_of=as_of)
        scores: dict[str, float] = {}
        for ind in fw.indicators:
            values = []
            for sheet in sheets:
                for entry in sheet.entries:
                    if entry.indicator_id == ind.indicator_id:
                        values.append(entry.score)
            if values:
                scores[ind.indicator_id] = sum(values) / len(values)
        resolutions = [
            s
            for s in self.store.sheets.values()
            if s.case_id == case.case_id and s.kind is SheetKind.REVIEW_RESOLUTION
            and (as_of is None or s.submitted_on <= as_of)
        ]
        if resolutions:
            latest = max(resolutions, key=lambda s: (s.scoring_round, s.submitted_on))
            for entry in latest.entries:
                scores[entry.indicator_id] = entry.score
        return scores

    def _dimension_averages(
        self, case: CertificationCase, as_of: Optional[date] = None
    ) -> dict[Dimension, float]:
        fw = self._case_framework(case)
        scores = self._final_indicator_scores(case, as_of=as_of)
        averages: dict[Dimension, float] = {}
        for dim in Dimension:
            total = 0.0
            weight_sum = 0.0
            for ind in fw.indicators:
                if ind.dimension is dim and ind.indicator_id in scores:
                    total += scores[ind.indicator_id] * ind.weight
                    weight_sum += ind.weight
            if weight_sum:
                averages[dim] = total / weight_sum
        return averages

    def dimension_gaps(self, case_id: str, as_of: Optional[date] = None) -> tuple[DimensionGap, ...]:
        case = self._case(case_id)
        fw = self._case_framework(case)
        averages = self._dimension_averages(case, as_of=as_of)
        gaps = []
        for dim in Dimension:
            threshold = fw.dimension_thresholds[dim]
            avg = averages.get(dim)
            gaps.append(
                DimensionGap(
                    dimension=dim,
                    average=avg,
                    threshold=threshold,
                    gap=None if avg is None else avg - threshold,
                    meets_threshold=avg is not None and avg >= threshold,
                )
            )
        return tuple(gaps)

    # ------------------------------------------------------------------
    # 认证决定与证书
    # ------------------------------------------------------------------
    def make_decision(
        self,
        case_id: str,
        decision_id: str,
        outcome: DecisionOutcome,
        rationale: str,
        decided_on: date,
        supersedes_decision_id: Optional[str] = None,
        new_evidence_ids: Sequence[str] = (),
        conditions: Optional[str] = None,
        conditions_due: Optional[date] = None,
    ) -> Decision:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.SCORES_CONFIRMED}, "作出认证决定")
            if decision_id in self.store.decisions:
                raise ValidationError(f"决定已存在: {decision_id}")
            if not rationale:
                raise DecisionError("认证决定必须说明理由")
            fw = self._case_framework(case)
            if not fw.is_effective_on(decided_on):
                raise FrameworkExpiredError(
                    f"框架 {fw.framework_id} v{fw.version} 在 {decided_on} 已过期，不能签发新结论"
                )
            new_evidence_ids = tuple(new_evidence_ids)
            if case.decision_ids:
                # 复评：必须引用原决定与新增证据
                last_id = case.decision_ids[-1]
                if supersedes_decision_id != last_id:
                    raise DecisionError("复评必须引用原决定")
                if not new_evidence_ids:
                    raise DecisionError("复评必须包含新增证据")
                original = self.store.decisions[last_id]
                for eid in new_evidence_ids:
                    evidence = self.store.evidence.get(eid)
                    if evidence is None or evidence.case_id != case_id:
                        raise DecisionError(f"新增证据 {eid} 不属于本案")
                    if evidence.recorded_on <= original.decided_on:
                        raise DecisionError(f"证据 {eid} 并非原决定之后的新增证据")
            else:
                if supersedes_decision_id is not None or new_evidence_ids:
                    raise DecisionError("首次决定不得引用原决定或复评证据")
            averages = self._dimension_averages(case)
            if outcome is DecisionOutcome.PASS:
                shortfalls = [
                    f"{dim.value} {averages[dim]:.2f}<{fw.dimension_thresholds[dim]:.2f}"
                    for dim in Dimension
                    if averages.get(dim) is None or averages[dim] < fw.dimension_thresholds[dim]
                ]
                if shortfalls:
                    raise DecisionError("存在未达合格线的维度，不能通过: " + "；".join(shortfalls))
            if outcome is DecisionOutcome.CONDITIONAL_PASS and (not conditions or conditions_due is None):
                raise DecisionError("附条件通过必须写明条件与履行期限")
            decision = Decision(
                decision_id=decision_id,
                case_id=case_id,
                outcome=outcome,
                rationale=rationale,
                decided_on=decided_on,
                framework_id=fw.framework_id,
                framework_version=fw.version,
                dimension_averages=averages,
                supersedes_decision_id=supersedes_decision_id,
                new_evidence_ids=new_evidence_ids,
                conditions=conditions,
                conditions_due=conditions_due,
            )
            # 全部校验通过后再统一写入，失败不留残留
            certificate = None
            if outcome is DecisionOutcome.PASS:
                certificate = self._issue_certificate_locked(case, decision, decided_on)
            self.store.decisions[decision_id] = decision
            case = replace(case, decision_ids=case.decision_ids + (decision_id,))
            if certificate is not None:
                self.store.certificates[certificate.certificate_id] = certificate
                case = self._transition(case, CaseStatus.CLOSED, decided_on, "通过并发证")
            else:
                case = self._transition(case, CaseStatus.DECIDED, decided_on, outcome.value)
            self._save_case(case)
            self._persist()
            return decision

    def _issue_certificate_locked(
        self, case: CertificationCase, decision: Decision, issued_on: date
    ) -> Certificate:
        existing = self.valid_certificate_for(case.candidate_id)
        if existing is not None:
            raise DuplicateCertificateError(
                f"候选人已持有有效证书 {existing.certificate_id}，不得重复签发"
            )
        return Certificate(
            certificate_id=f"CERT-{case.case_id}-{decision.decision_id}",
            candidate_id=case.candidate_id,
            case_id=case.case_id,
            decision_id=decision.decision_id,
            issued_on=issued_on,
        )

    def revoke_certificate(self, certificate_id: str, revoked_on: date) -> Certificate:
        with self._lock:
            cert = self.store.certificates.get(certificate_id)
            if cert is None:
                raise NotFoundError(f"证书不存在: {certificate_id}")
            if cert.status is not CertificateStatus.VALID:
                raise InvalidStateError("证书已注销")
            cert = replace(cert, status=CertificateStatus.REVOKED, revoked_on=revoked_on)
            self.store.certificates[certificate_id] = cert
            self._persist()
            return cert

    def valid_certificate_for(self, candidate_id: str, as_of: Optional[date] = None) -> Optional[Certificate]:
        for cert in self.store.certificates.values():
            if cert.candidate_id != candidate_id:
                continue
            if as_of is None:
                if cert.status is CertificateStatus.VALID:
                    return cert
                continue
            if cert.issued_on > as_of:
                continue
            if cert.status is CertificateStatus.VALID or (
                cert.revoked_on is not None and cert.revoked_on > as_of
            ):
                return cert
        return None

    # ------------------------------------------------------------------
    # 补强计划
    # ------------------------------------------------------------------
    def create_remediation_plan(
        self,
        case_id: str,
        plan_id: str,
        steps: Sequence[RemediationStep],
        created_on: date,
    ) -> RemediationPlan:
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.DECIDED}, "制定补强计划")
            last = self.store.decisions[case.decision_ids[-1]]
            if last.outcome is not DecisionOutcome.REMEDIATION_THEN_REVIEW:
                raise InvalidStateError("只有「补强后复评」的决定可以制定补强计划")
            if case.remediation_plan_id is not None:
                raise InvalidStateError("本案已有补强计划")
            if plan_id in self.store.plans:
                raise ValidationError(f"补强计划已存在: {plan_id}")
            steps = tuple(steps)
            if not steps:
                raise ValidationError("补强计划至少包含一个步骤")
            ids = [s.step_id for s in steps]
            if len(set(ids)) != len(ids) or not all(ids):
                raise ValidationError("步骤标识重复或为空")
            known = set(ids)
            for s in steps:
                if not s.title:
                    raise ValidationError("步骤标题不能为空")
                if not s.mentor_id:
                    raise ValidationError(f"步骤 {s.step_id} 必须指定导师")
                unknown = set(s.depends_on) - known
                if unknown:
                    raise ValidationError(f"步骤 {s.step_id} 依赖未知步骤: {sorted(unknown)}")
                if s.deadline < created_on:
                    raise ValidationError(f"步骤 {s.step_id} 的完成期限早于计划创建日")
            self._check_acyclic(steps)
            plan = RemediationPlan(plan_id=plan_id, case_id=case_id, created_on=created_on, steps=steps)
            self.store.plans[plan_id] = plan
            case = replace(case, remediation_plan_id=plan_id)
            case = self._transition(case, CaseStatus.REMEDIATION, created_on, "进入补强")
            self._save_case(case)
            self._persist()
            return plan

    @staticmethod
    def _check_acyclic(steps: tuple[RemediationStep, ...]) -> None:
        graph = {s.step_id: list(s.depends_on) for s in steps}
        state: dict[str, int] = {}

        def visit(node: str, stack: tuple[str, ...]) -> None:
            state[node] = 1
            for dep in graph[node]:
                if state.get(dep) == 1:
                    raise ValidationError("补强步骤存在循环依赖: " + " -> ".join(stack + (dep,)))
                if state.get(dep) is None:
                    visit(dep, stack + (dep,))
            state[node] = 2

        for s in steps:
            if state.get(s.step_id) is None:
                visit(s.step_id, (s.step_id,))

    def submit_step_completion(self, plan_id: str, step_id: str, on: date) -> RemediationPlan:
        with self._lock:
            plan = self._plan(plan_id)
            step = self._plan_step(plan, step_id)
            if step.status is not StepStatus.PENDING:
                raise InvalidStateError(f"步骤 {step_id} 当前为「{step.status.value}」，无法提交完成")
            blockers = [
                dep
                for dep in step.depends_on
                if plan.step(dep).status is not StepStatus.CONFIRMED
            ]
            if blockers:
                raise DependencyError(f"前置步骤尚未经导师确认: {blockers}")
            if on > step.deadline:
                raise StepOverdueError(f"步骤 {step_id} 的完成期限 {step.deadline} 已过")
            return self._update_step(plan, step_id, replace(step, status=StepStatus.SUBMITTED, submitted_on=on))

    def confirm_step(self, plan_id: str, step_id: str, mentor_id: str, on: date) -> RemediationPlan:
        with self._lock:
            plan = self._plan(plan_id)
            step = self._plan_step(plan, step_id)
            if step.status is not StepStatus.SUBMITTED:
                raise InvalidStateError(f"步骤 {step_id} 尚未提交完成，无法确认")
            if mentor_id != step.mentor_id:
                raise ValidationError(f"步骤 {step_id} 须由导师 {step.mentor_id} 确认")
            plan = self._update_step(
                plan,
                step_id,
                replace(step, status=StepStatus.CONFIRMED, confirmed_on=on, confirmed_by=mentor_id),
            )
            if plan.fulfilled:
                case = self._case(plan.case_id)
                if case.status is CaseStatus.REMEDIATION:
                    case = self._transition(case, CaseStatus.RE_REVIEW, on, "补强计划已完成，待复评")
                    self._save_case(case)
                    self._persist()
            return plan

    def _plan(self, plan_id: str) -> RemediationPlan:
        plan = self.store.plans.get(plan_id)
        if plan is None:
            raise NotFoundError(f"补强计划不存在: {plan_id}")
        return plan

    @staticmethod
    def _plan_step(plan: RemediationPlan, step_id: str) -> RemediationStep:
        step = plan.step(step_id)
        if step is None:
            raise NotFoundError(f"补强步骤不存在: {step_id}")
        return step

    def _update_step(self, plan: RemediationPlan, step_id: str, new_step: RemediationStep) -> RemediationPlan:
        plan = replace(
            plan, steps=tuple(new_step if s.step_id == step_id else s for s in plan.steps)
        )
        self.store.plans[plan.plan_id] = plan
        self._persist()
        return plan

    def begin_re_review(self, case_id: str, on: date) -> CertificationCase:
        """附条件通过的案件在条件核查时进入复评。"""
        with self._lock:
            case = self._case(case_id)
            self._require_status(case, {CaseStatus.DECIDED}, "进入复评")
            last = self.store.decisions[case.decision_ids[-1]]
            if last.outcome is not DecisionOutcome.CONDITIONAL_PASS:
                raise InvalidStateError("只有「附条件通过」的决定可以据此进入复评")
            case = self._transition(case, CaseStatus.RE_REVIEW, on, "核查附条件履行情况")
            self._save_case(case)
            self._persist()
            return case

    # ------------------------------------------------------------------
    # 评审秘书查询
    # ------------------------------------------------------------------
    def certification_snapshot(self, candidate_id: str, as_of: date) -> CertificationSnapshot:
        if candidate_id not in self.store.candidates:
            raise NotFoundError(f"候选人不存在: {candidate_id}")
        cases = sorted(
            (c for c in self.store.cases.values() if c.candidate_id == candidate_id),
            key=lambda c: c.opened_on,
        )
        case = next((c for c in reversed(cases) if c.opened_on <= as_of), None)
        if case is None:
            return CertificationSnapshot(
                candidate_id=candidate_id,
                as_of=as_of,
                case_id=None,
                case_status=None,
                effective_decision=None,
                dimension_gaps=(),
                calibration_trail=(),
                outstanding_steps=(),
                certificate=None,
            )
        status_events = [e for e in case.history if e.on <= as_of]
        case_status = status_events[-1].status if status_events else None
        effective_decision = None
        for decision_id in case.decision_ids:
            decision = self.store.decisions[decision_id]
            if decision.decided_on <= as_of:
                effective_decision = decision
        calibration_trail = tuple(
            replace(s, status=s.status_on(as_of))
            for s in self.store.calibrations.values()
            if s.case_id == case.case_id and s.opened_on <= as_of
        )
        outstanding: list[RemediationStep] = []
        if case.remediation_plan_id is not None:
            plan = self.store.plans[case.remediation_plan_id]
            outstanding = [
                s for s in plan.steps if s.confirmed_on is None or s.confirmed_on > as_of
            ]
        return CertificationSnapshot(
            candidate_id=candidate_id,
            as_of=as_of,
            case_id=case.case_id,
            case_status=case_status,
            effective_decision=effective_decision,
            dimension_gaps=self.dimension_gaps(case.case_id, as_of=as_of),
            calibration_trail=calibration_trail,
            outstanding_steps=tuple(outstanding),
            certificate=self.valid_certificate_for(candidate_id, as_of=as_of),
        )

    # ------------------------------------------------------------------
    # 只读访问
    # ------------------------------------------------------------------
    def get_case(self, case_id: str) -> CertificationCase:
        return self._case(case_id)

    def get_framework(self, framework_id: str, version: int) -> FrameworkVersion:
        return self._framework(framework_id, version)

    def get_decision(self, decision_id: str) -> Decision:
        decision = self.store.decisions.get(decision_id)
        if decision is None:
            raise NotFoundError(f"决定不存在: {decision_id}")
        return decision

    def get_plan(self, plan_id: str) -> RemediationPlan:
        return self._plan(plan_id)

    def case_evidence(self, case_id: str) -> tuple[EvidenceItem, ...]:
        return tuple(e for e in self.store.evidence.values() if e.case_id == case_id)
