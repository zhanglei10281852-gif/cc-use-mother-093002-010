"""认证应用服务：编排框架、证据、评分、校准、决定、补强与证书。"""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

from .calibration import (
    CalibrationSession,
    CalibrationStatus,
    ReviewRequest,
    ReviewStatus,
    ScoreRevision,
)
from .case import CaseStatus, CertificationCase
from .certificate import Certificate, CertificateStatus
from .decision import CertificationDecision, DecisionOutcome
from .errors import ConcurrencyError, NotFoundError, StateError, ValidationError
from .evidence import Evidence, EvidenceKind, ObservationTask, TaskStatus
from .framework import (
    DecisionPolicy,
    Dimension,
    Framework,
    FrameworkVersion,
    Indicator,
)
from .people import Assessor, Candidate
from .remediation import RemediationPlan, RemediationStep
from .scoring import (
    ConflictDeclaration,
    ScoreEntry,
    ScoreSheet,
    SheetStatus,
    aggregate_dimension_scores,
    detect_deviations,
)
from .store import JsonStore


class CertificationService:
    """教师三维能力认证服务的应用入口。

    所有变更在单仓储锁内完成并即时落盘，因此服务重启后
    待校准等中间状态保持原样；证书签发在同一锁内做
    检查并插入，保证并发下不会出现两张有效证书。
    """

    def __init__(self, store_path: str | Path):
        self._store = JsonStore(store_path)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 基础查找
    # ------------------------------------------------------------------
    def _get(self, collection: str, key: str, label: str):
        try:
            return self._store.collection(collection)[key]
        except KeyError:
            raise NotFoundError(f"{label}不存在: {key}") from None

    def get_framework(self, framework_id: str) -> Framework:
        return self._get("frameworks", framework_id, "框架")

    def get_case(self, case_id: str) -> CertificationCase:
        return self._get("cases", case_id, "案件")

    def get_assessor(self, assessor_id: str) -> Assessor:
        return self._get("assessors", assessor_id, "评委")

    def get_candidate(self, candidate_id: str) -> Candidate:
        return self._get("candidates", candidate_id, "候选人")

    def get_decision(self, decision_id: str) -> CertificationDecision:
        return self._get("decisions", decision_id, "决定")

    def get_plan(self, plan_id: str) -> RemediationPlan:
        return self._get("plans", plan_id, "补强计划")

    def case_version(self, case: CertificationCase) -> FrameworkVersion:
        return self.get_framework(case.framework_id).version(case.framework_revision)

    # ------------------------------------------------------------------
    # 框架与版本
    # ------------------------------------------------------------------
    def create_framework(self, name: str) -> str:
        with self._lock:
            framework_id = self._store.next_id("FW")
            self._store.collection("frameworks")[framework_id] = Framework(framework_id, name)
            self._store.save()
            return framework_id

    def add_framework_version(
        self,
        framework_id: str,
        title: str,
        indicators: list[Indicator],
        effective_from: date,
        effective_to: date | None = None,
        policy: DecisionPolicy | None = None,
        publish: bool = True,
    ) -> int:
        with self._lock:
            framework = self.get_framework(framework_id)
            version = FrameworkVersion(
                framework_id=framework_id,
                revision=len(framework.versions) + 1,
                title=title,
                indicators=tuple(indicators),
                effective_from=effective_from,
                effective_to=effective_to,
                policy=policy or DecisionPolicy(),
                published=publish,
            )
            framework.add_version(version)
            self._store.save()
            return version.revision

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    def register_candidate(self, name: str) -> str:
        with self._lock:
            candidate_id = self._store.next_id("CAND")
            self._store.collection("candidates")[candidate_id] = Candidate(candidate_id, name)
            self._store.save()
            return candidate_id

    def register_assessor(
        self, name: str, dimensions: set[Dimension], is_mentor: bool = False
    ) -> str:
        if not dimensions:
            raise ValidationError("评委至少具备一个维度的资质")
        with self._lock:
            assessor_id = self._store.next_id("ASR")
            self._store.collection("assessors")[assessor_id] = Assessor(
                assessor_id, name, frozenset(dimensions), is_mentor=is_mentor
            )
            self._store.save()
            return assessor_id

    # ------------------------------------------------------------------
    # 案件与证据
    # ------------------------------------------------------------------
    def open_case(
        self, candidate_id: str, framework_id: str, assessor_ids: list[str], on: date
    ) -> str:
        with self._lock:
            self.get_candidate(candidate_id)
            framework = self.get_framework(framework_id)
            version = framework.active_version(on)
            for other in self._cases_of(candidate_id):
                if other.status is not CaseStatus.CLOSED:
                    raise StateError(f"候选人已有未结案案件: {other.case_id}")
            assessors = [self.get_assessor(a) for a in assessor_ids]
            if len(assessors) < 2:
                raise ValidationError("一个案件至少需要两名评委")
            if len(set(assessor_ids)) != len(assessor_ids):
                raise ValidationError("评委不能重复指派")
            for assessor in assessors:
                if not assessor.active:
                    raise ValidationError(f"评委 {assessor.assessor_id} 已停用")
            for dimension in Dimension:
                qualified = [a for a in assessors if dimension in a.qualified_dimensions]
                if len(qualified) < 2:
                    raise ValidationError(
                        f"{dimension.value} 维度至少需要两名具备资质的评委"
                    )
            case_id = self._store.next_id("CASE")
            self._store.collection("cases")[case_id] = CertificationCase(
                case_id=case_id,
                candidate_id=candidate_id,
                framework_id=framework_id,
                framework_revision=version.revision,
                assessor_ids=tuple(assessor_ids),
                opened_on=on,
            )
            self._store.save()
            return case_id

    def plan_observation_task(
        self,
        case_id: str,
        assessor_id: str,
        indicator_codes: list[str],
        planned_date: date,
    ) -> str:
        with self._lock:
            case = self.get_case(case_id)
            if case.status is CaseStatus.CLOSED:
                raise StateError("案件已结案")
            if assessor_id not in case.assessor_ids:
                raise ValidationError("观察人必须是案件指派的评委")
            version = self.case_version(case)
            for code in indicator_codes:
                version.indicator(code)
            task_id = self._store.next_id("OBS")
            self._store.collection("tasks")[task_id] = ObservationTask(
                task_id=task_id,
                case_id=case_id,
                assessor_id=assessor_id,
                indicator_codes=tuple(indicator_codes),
                planned_date=planned_date,
            )
            self._store.save()
            return task_id

    def complete_observation_task(self, task_id: str, summary: str, on: date) -> str:
        with self._lock:
            task = self._get("tasks", task_id, "观察任务")
            if task.status is not TaskStatus.PLANNED:
                raise StateError("观察任务不在已计划状态")
            task.status = TaskStatus.COMPLETED
            task.completed_date = on
            case = self.get_case(task.case_id)
            evidence_id = self._record_evidence(
                case=case,
                kind=EvidenceKind.CLASSROOM_OBSERVATION,
                indicator_codes=task.indicator_codes,
                submitted_by=task.assessor_id,
                summary=summary,
                on=on,
                task_id=task_id,
            )
            self._store.save()
            return evidence_id

    def submit_evidence(
        self,
        case_id: str,
        kind: EvidenceKind,
        indicator_codes: list[str],
        submitted_by: str,
        summary: str,
        on: date,
    ) -> str:
        with self._lock:
            case = self.get_case(case_id)
            evidence_id = self._record_evidence(
                case, kind, tuple(indicator_codes), submitted_by, summary, on, None
            )
            self._store.save()
            return evidence_id

    def _record_evidence(
        self,
        case: CertificationCase,
        kind: EvidenceKind,
        indicator_codes,
        submitted_by: str,
        summary: str,
        on: date,
        task_id: str | None,
    ) -> str:
        if case.status is CaseStatus.CLOSED:
            raise StateError("案件已结案，不能再登记证据")
        if not indicator_codes:
            raise ValidationError("证据至少归入一条指标")
        version = self.case_version(case)
        for code in indicator_codes:
            version.indicator(code)
        evidence_id = self._store.next_id("EVD")
        self._store.collection("evidence")[evidence_id] = Evidence(
            evidence_id=evidence_id,
            case_id=case.case_id,
            kind=kind,
            indicator_codes=tuple(indicator_codes),
            framework_id=case.framework_id,
            framework_revision=case.framework_revision,
            submitted_by=submitted_by,
            submitted_on=on,
            summary=summary,
            task_id=task_id,
        )
        return evidence_id

    # ------------------------------------------------------------------
    # 评分
    # ------------------------------------------------------------------
    def begin_scoring(self, case_id: str) -> None:
        with self._lock:
            case = self.get_case(case_id)
            if case.status is not CaseStatus.OPEN:
                raise StateError("只有立项状态的案件可以开始评分")
            case.status = CaseStatus.SCORING
            self._store.save()

    def declare_conflict(
        self,
        case_id: str,
        assessor_id: str,
        has_conflict: bool,
        detail: str,
        on: date,
    ) -> None:
        with self._lock:
            case = self.get_case(case_id)
            if assessor_id not in case.assessor_ids:
                raise ValidationError("申报人必须是案件指派的评委")
            key = f"{case_id}:{assessor_id}"
            existing = self._store.collection("declarations").get(key)
            if existing is not None and self._sheet_of(case_id, assessor_id, case.round_no):
                raise StateError("评分表已提交，不能再修改利益冲突申报")
            if has_conflict and not detail:
                raise ValidationError("存在利益冲突时必须说明详情")
            self._store.collection("declarations")[key] = ConflictDeclaration(
                case_id=case_id,
                assessor_id=assessor_id,
                has_conflict=has_conflict,
                detail=detail,
                declared_on=on,
            )
            self._store.save()

    def submit_scores(
        self, case_id: str, assessor_id: str, scores: dict[str, float], on: date
    ) -> str:
        with self._lock:
            case = self.get_case(case_id)
            if case.status is not CaseStatus.SCORING:
                raise StateError("案件不在评分阶段")
            if assessor_id not in case.assessor_ids:
                raise ValidationError("评分人必须是案件指派的评委")
            declaration = self._store.collection("declarations").get(
                f"{case_id}:{assessor_id}"
            )
            if declaration is None:
                raise StateError("评分前必须先申报利益冲突")
            assessor = self.get_assessor(assessor_id)
            version = self.case_version(case)
            expected = {
                i.code for i in version.indicators if assessor.qualified_for(i.dimension)
            }
            if not expected:
                raise ValidationError(f"评委 {assessor_id} 不具备本案任何维度的资质")
            if set(scores) != expected:
                raise ValidationError("评分必须且只能覆盖评委资质范围内的指标")
            for value in scores.values():
                if not 0 <= value <= 100:
                    raise ValidationError("评分必须在 0 到 100 之间")
            if self._sheet_of(case_id, assessor_id, case.round_no):
                raise StateError("该评委本轮已提交评分表")
            sheet_id = self._store.next_id("SH")
            status = SheetStatus.EXCLUDED if declaration.has_conflict else SheetStatus.SUBMITTED
            self._store.collection("sheets")[sheet_id] = ScoreSheet(
                sheet_id=sheet_id,
                case_id=case_id,
                assessor_id=assessor_id,
                round_no=case.round_no,
                entries=tuple(ScoreEntry(code, float(value)) for code, value in scores.items()),
                submitted_on=on,
                status=status,
            )
            self._finalize_round_if_complete(case, on)
            self._store.save()
            return sheet_id

    def submitted_scores(self, case_id: str) -> list[ScoreSheet]:
        """评分表在评分阶段对评委保密，密封期结束后才可查阅。"""
        case = self.get_case(case_id)
        if case.status is CaseStatus.SCORING:
            raise StateError("评分仍在进行，评分表处于密封状态")
        return self._round_sheets(case, case.round_no)

    def _sheet_of(self, case_id: str, assessor_id: str, round_no: int) -> ScoreSheet | None:
        for sheet in self._store.collection("sheets").values():
            if (
                sheet.case_id == case_id
                and sheet.assessor_id == assessor_id
                and sheet.round_no == round_no
            ):
                return sheet
        return None

    def _round_sheets(self, case: CertificationCase, round_no: int) -> list[ScoreSheet]:
        return [
            s
            for s in self._store.collection("sheets").values()
            if s.case_id == case.case_id and s.round_no == round_no
        ]

    def _included_sheets(self, case: CertificationCase, round_no: int) -> list[ScoreSheet]:
        return [s for s in self._round_sheets(case, round_no) if s.status is SheetStatus.SUBMITTED]

    def _finalize_round_if_complete(self, case: CertificationCase, on: date) -> None:
        submitted = {
            s.assessor_id for s in self._round_sheets(case, case.round_no)
        }
        if set(case.assessor_ids) - submitted:
            return
        included = self._included_sheets(case, case.round_no)
        version = self.case_version(case)
        deviations = detect_deviations(version, included) if len(included) >= 2 else []
        if deviations:
            session_id = self._store.next_id("CAL")
            self._store.collection("calibrations")[session_id] = CalibrationSession(
                session_id=session_id,
                case_id=case.case_id,
                round_no=case.round_no,
                indicator_codes=tuple(d.indicator_code for d in deviations),
                opened_on=on,
            )
            case.status = CaseStatus.CALIBRATION
        else:
            case.status = CaseStatus.DECISION_READY

    # ------------------------------------------------------------------
    # 校准与复核
    # ------------------------------------------------------------------
    def submit_calibration_revision(
        self,
        session_id: str,
        assessor_id: str,
        revisions: dict[str, float],
        on: date,
    ) -> None:
        with self._lock:
            session = self._get("calibrations", session_id, "校准会话")
            if session.status is not CalibrationStatus.OPEN:
                raise StateError("校准会话已关闭")
            case = self.get_case(session.case_id)
            if assessor_id not in case.assessor_ids:
                raise ValidationError("修订人必须是案件指派的评委")
            assessor = self.get_assessor(assessor_id)
            version = self.case_version(case)
            unknown = set(revisions) - set(session.indicator_codes)
            if unknown:
                raise ValidationError(f"只能修订偏离指标: {sorted(unknown)}")
            for code, value in revisions.items():
                indicator = version.indicator(code)
                if not assessor.qualified_for(indicator.dimension):
                    raise ValidationError(
                        f"评委 {assessor_id} 不具备 {indicator.dimension.value} 维度资质"
                    )
                if not 0 <= value <= 100:
                    raise ValidationError("评分必须在 0 到 100 之间")
            session.revisions = [
                r
                for r in session.revisions
                if not (r.assessor_id == assessor_id and r.indicator_code in revisions)
            ]
            for code, value in revisions.items():
                session.revisions.append(
                    ScoreRevision(assessor_id, code, float(value), on)
                )
            self._store.save()

    def resolve_calibration(self, session_id: str, note: str, on: date) -> str | None:
        """校准收口：偏离收敛则进入待决定，否则转复核并返回复核编号。"""
        with self._lock:
            session = self._get("calibrations", session_id, "校准会话")
            if session.status is not CalibrationStatus.OPEN:
                raise StateError("校准会话已关闭")
            case = self.get_case(session.case_id)
            version = self.case_version(case)
            included = self._included_sheets(case, session.round_no)
            remaining = self._remaining_deviations(version, included, session)
            session.closed_on = on
            if not remaining:
                session.status = CalibrationStatus.RESOLVED
                session.resolution_note = note
                case.status = CaseStatus.DECISION_READY
                self._store.save()
                return None
            session.status = CalibrationStatus.ESCALATED
            session.resolution_note = note
            review_id = self._store.next_id("REV")
            self._store.collection("reviews")[review_id] = ReviewRequest(
                review_id=review_id,
                case_id=case.case_id,
                round_no=session.round_no,
                session_id=session_id,
                indicator_codes=tuple(remaining),
                opened_on=on,
            )
            case.status = CaseStatus.REVIEW
            self._store.save()
            return review_id

    def _remaining_deviations(self, version, included, session: CalibrationSession) -> list[str]:
        from .scoring import effective_values

        values = effective_values(included, tuple(session.revisions))
        remaining = []
        for code in session.indicator_codes:
            per_assessor = [v[code] for v in values.values() if code in v]
            if len(per_assessor) >= 2 and max(per_assessor) - min(per_assessor) > (
                version.policy.score_spread_threshold
            ):
                remaining.append(code)
        return remaining

    def resolve_review(
        self,
        review_id: str,
        reviewer_id: str,
        final_scores: dict[str, float],
        note: str,
        on: date,
    ) -> None:
        with self._lock:
            review = self._get("reviews", review_id, "复核请求")
            if review.status is not ReviewStatus.OPEN:
                raise StateError("复核已办结")
            case = self.get_case(review.case_id)
            if reviewer_id in case.assessor_ids:
                raise ValidationError("复核人必须是未参与本案评分的评委")
            reviewer = self.get_assessor(reviewer_id)
            if not reviewer.active:
                raise ValidationError("复核人已停用")
            version = self.case_version(case)
            if set(final_scores) != set(review.indicator_codes):
                raise ValidationError("复核裁定必须覆盖全部待复核指标")
            for code, value in final_scores.items():
                indicator = version.indicator(code)
                if not reviewer.qualified_for(indicator.dimension):
                    raise ValidationError(
                        f"复核人不具备 {indicator.dimension.value} 维度资质"
                    )
                if not 0 <= value <= 100:
                    raise ValidationError("评分必须在 0 到 100 之间")
            review.reviewer_id = reviewer_id
            review.final_scores = dict(final_scores)
            review.note = note
            review.status = ReviewStatus.RESOLVED
            review.resolved_on = on
            case.status = CaseStatus.DECISION_READY
            self._store.save()

    # ------------------------------------------------------------------
    # 决定
    # ------------------------------------------------------------------
    def compute_dimension_scores(self, case_id: str) -> dict[Dimension, float]:
        case = self.get_case(case_id)
        version = self.case_version(case)
        included = self._included_sheets(case, case.round_no)
        revisions, overrides = self._round_adjustments(case)
        return aggregate_dimension_scores(version, included, revisions, overrides)

    def recommend_outcome(self, case_id: str) -> DecisionOutcome:
        case = self.get_case(case_id)
        version = self.case_version(case)
        scores = self.compute_dimension_scores(case_id)
        worst_deficit = max(
            (version.dimension_requirement(d) - scores[d] for d in Dimension),
            default=0.0,
        )
        if worst_deficit <= 0:
            return DecisionOutcome.PASS
        if worst_deficit <= version.policy.conditional_margin:
            return DecisionOutcome.CONDITIONAL_PASS
        if worst_deficit <= version.policy.remediation_floor:
            return DecisionOutcome.REASSESS_AFTER_REMEDIATION
        return DecisionOutcome.FAIL

    def record_decision(
        self,
        case_id: str,
        rationale: str,
        on: date,
        outcome: DecisionOutcome | None = None,
        supersedes: str | None = None,
        new_evidence_ids: list[str] | None = None,
    ) -> str:
        with self._lock:
            case = self.get_case(case_id)
            if case.status is not CaseStatus.DECISION_READY:
                raise StateError("案件不在待决定状态")
            version = self.case_version(case)
            if version.is_expired(on):
                raise StateError("框架版本已过期，不能签发新结论")
            if len(self._included_sheets(case, case.round_no)) < 2:
                raise StateError("有效评分表不足两份，无法形成决定")
            recommended = self.recommend_outcome(case_id)
            if outcome is None:
                outcome = recommended
            elif outcome is not recommended and not rationale:
                raise ValidationError("决定与系统评定不一致时必须说明理由")
            previous_ids = list(case.decision_ids)
            if supersedes is not None:
                if not previous_ids or supersedes != previous_ids[-1]:
                    raise ValidationError("复评必须引用本案当前有效的原决定")
                original = self.get_decision(supersedes)
                if not new_evidence_ids:
                    raise ValidationError("复评必须提供新增证据")
                for evidence_id in new_evidence_ids:
                    evidence = self._get("evidence", evidence_id, "证据")
                    if evidence.case_id != case_id:
                        raise ValidationError("新增证据必须属于本案")
                    if evidence.submitted_on <= original.decided_on:
                        raise ValidationError("新增证据必须晚于原决定日期提交")
            elif previous_ids:
                raise ValidationError("本案已有决定，再次决定必须引用原决定")
            scores = self.compute_dimension_scores(case_id)
            gaps = {
                d: scores[d] - version.dimension_requirement(d) for d in Dimension
            }
            decision_id = self._store.next_id("DEC")
            self._store.collection("decisions")[decision_id] = CertificationDecision(
                decision_id=decision_id,
                case_id=case_id,
                candidate_id=case.candidate_id,
                framework_id=case.framework_id,
                framework_revision=case.framework_revision,
                outcome=outcome,
                dimension_scores=scores,
                dimension_gaps=gaps,
                decided_on=on,
                rationale=rationale,
                supersedes=supersedes,
                new_evidence_ids=tuple(new_evidence_ids or ()),
            )
            case.decision_ids.append(decision_id)
            case.status = (
                CaseStatus.CLOSED if outcome is DecisionOutcome.FAIL else CaseStatus.DECIDED
            )
            self._store.save()
            return decision_id

    def _round_adjustments(self, case: CertificationCase):
        """当前轮次的校准修订与复核裁定值。"""
        revisions: tuple = ()
        overrides: dict[str, float] = {}
        for session in self._store.collection("calibrations").values():
            if (
                session.case_id == case.case_id
                and session.round_no == case.round_no
                and session.status in (CalibrationStatus.RESOLVED, CalibrationStatus.ESCALATED)
            ):
                revisions = tuple(session.revisions)
        for review in self._store.collection("reviews").values():
            if (
                review.case_id == case.case_id
                and review.round_no == case.round_no
                and review.status is ReviewStatus.RESOLVED
            ):
                overrides = dict(review.final_scores)
        return revisions, overrides

    # ------------------------------------------------------------------
    # 补强计划
    # ------------------------------------------------------------------
    def create_remediation_plan(
        self,
        decision_id: str,
        steps: list[dict],
        on: date,
    ) -> str:
        """steps: [{"key", "title", "indicator_codes", "depends_on", "deadline"}]"""
        with self._lock:
            decision = self.get_decision(decision_id)
            if decision.outcome not in (
                DecisionOutcome.CONDITIONAL_PASS,
                DecisionOutcome.REASSESS_AFTER_REMEDIATION,
            ):
                raise StateError("只有附条件通过或补强后复评的决定可以建立补强计划")
            for plan in self._store.collection("plans").values():
                if plan.decision_id == decision_id:
                    raise StateError("该决定已建立补强计划")
            if not steps:
                raise ValidationError("补强计划至少包含一个步骤")
            case = self.get_case(decision.case_id)
            version = self.case_version(case)
            plan_id = self._store.next_id("PLAN")
            key_to_step_id = {s["key"]: f"{plan_id}/S{i + 1}" for i, s in enumerate(steps)}
            if len(key_to_step_id) != len(steps):
                raise ValidationError("步骤标识不能重复")
            plan = RemediationPlan(plan_id, case.case_id, decision_id, on)
            for spec in steps:
                for code in spec.get("indicator_codes", ()):
                    version.indicator(code)
                depends_on = tuple(
                    key_to_step_id[d] for d in spec.get("depends_on", ()) if d in key_to_step_id
                )
                missing = [d for d in spec.get("depends_on", ()) if d not in key_to_step_id]
                if missing:
                    raise ValidationError(f"步骤依赖了不存在的标识: {missing}")
                plan.steps.append(
                    RemediationStep(
                        step_id=key_to_step_id[spec["key"]],
                        plan_id=plan_id,
                        title=spec["title"],
                        indicator_codes=tuple(spec.get("indicator_codes", ())),
                        depends_on=depends_on,
                        deadline=spec["deadline"],
                    )
                )
            plan.assert_dependencies_acyclic()
            self._store.collection("plans")[plan_id] = plan
            self._store.save()
            return plan_id

    def complete_remediation_step(self, step_id: str, mentor_id: str, on: date) -> None:
        with self._lock:
            plan = self._plan_of_step(step_id)
            step = plan.step(step_id)
            mentor = self.get_assessor(mentor_id)
            if not mentor.is_mentor or not mentor.active:
                raise ValidationError("确认人必须是在册的补强导师")
            plan.check_can_complete(step, on)
            step.completed_on = on
            step.mentor_id = mentor_id
            self._store.save()

    def _plan_of_step(self, step_id: str) -> RemediationPlan:
        for plan in self._store.collection("plans").values():
            for step in plan.steps:
                if step.step_id == step_id:
                    return plan
        raise NotFoundError(f"补强步骤不存在: {step_id}")

    def reopen_for_reassessment(self, case_id: str, on: date) -> None:
        """补强完成后开启新一轮评分，复评决定须引用原决定。"""
        with self._lock:
            case = self.get_case(case_id)
            if case.status is not CaseStatus.DECIDED:
                raise StateError("只有已决定的案件可以复评")
            latest = self.get_decision(case.decision_ids[-1])
            if latest.outcome is not DecisionOutcome.REASSESS_AFTER_REMEDIATION:
                raise StateError("只有补强后复评的决定可以开启复评")
            plan = self._plan_for_decision(latest.decision_id)
            if plan is None or not plan.is_complete():
                raise StateError("补强计划尚未全部完成，不能复评")
            case.round_no += 1
            case.status = CaseStatus.SCORING
            self._store.save()

    def _plan_for_decision(self, decision_id: str) -> RemediationPlan | None:
        for plan in self._store.collection("plans").values():
            if plan.decision_id == decision_id:
                return plan
        return None

    # ------------------------------------------------------------------
    # 证书
    # ------------------------------------------------------------------
    def issue_certificate(self, candidate_id: str, on: date) -> str:
        with self._lock:
            self.get_candidate(candidate_id)
            case = self._latest_decided_case(candidate_id)
            if case is None:
                raise StateError("候选人没有已决定的案件")
            decision = self.get_decision(case.decision_ids[-1])
            if decision.outcome is DecisionOutcome.PASS:
                pass
            elif decision.outcome is DecisionOutcome.CONDITIONAL_PASS:
                plan = self._plan_for_decision(decision.decision_id)
                if plan is None or not plan.is_complete():
                    raise StateError("附条件通过的补强计划尚未全部完成")
            else:
                raise StateError("当前决定不构成发证条件")
            version = self.case_version(case)
            if version.is_expired(on):
                raise StateError("框架版本已过期，不能签发证书")
            for certificate in self._store.collection("certificates").values():
                if (
                    certificate.candidate_id == candidate_id
                    and certificate.status is CertificateStatus.ACTIVE
                ):
                    raise ConcurrencyError("该候选人已持有有效证书")
            certificate_id = self._store.next_id("CERT")
            serial = f"CERT-{on.year}-{self._store.next_id('SN')}"
            self._store.collection("certificates")[certificate_id] = Certificate(
                certificate_id=certificate_id,
                candidate_id=candidate_id,
                case_id=case.case_id,
                decision_id=decision.decision_id,
                serial_no=serial,
                issued_on=on,
            )
            case.status = CaseStatus.CLOSED
            self._store.save()
            return certificate_id

    def revoke_certificate(self, certificate_id: str) -> None:
        with self._lock:
            certificate = self._get("certificates", certificate_id, "证书")
            if certificate.status is not CertificateStatus.ACTIVE:
                raise StateError("证书已注销")
            certificate.status = CertificateStatus.REVOKED
            self._store.save()

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------
    def _cases_of(self, candidate_id: str) -> list[CertificationCase]:
        return [
            c
            for c in self._store.collection("cases").values()
            if c.candidate_id == candidate_id
        ]

    def _latest_decided_case(self, candidate_id: str) -> CertificationCase | None:
        decided = [
            c
            for c in self._cases_of(candidate_id)
            if c.decision_ids and c.status in (CaseStatus.DECIDED, CaseStatus.CLOSED)
        ]
        if not decided:
            return None
        return max(decided, key=lambda c: self.get_decision(c.decision_ids[-1]).decided_on)

    def store(self) -> JsonStore:
        return self._store
