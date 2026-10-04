"""评审秘书的时点查询：给定候选人与日期，还原当时有效的认证全景。"""
from __future__ import annotations

from datetime import date

from .case import CertificationCase
from .errors import NotFoundError
from .framework import Dimension
from .service import CertificationService


def _decision_as_of(service: CertificationService, case: CertificationCase, as_of: date):
    decisions = [
        service.get_decision(d)
        for d in case.decision_ids
        if service.get_decision(d).decided_on <= as_of
    ]
    if not decisions:
        return None
    return max(decisions, key=lambda d: d.decided_on)


def secretary_briefing(
    service: CertificationService, candidate_id: str, as_of: date
) -> dict:
    """候选人在指定日期的认证快照。

    返回当时有效的决定、三维差距、评委校准过程，
    以及截至该日期仍未完成的补强步骤（含逾期标记）。
    """
    candidate = service.get_candidate(candidate_id)
    cases = sorted(service._cases_of(candidate_id), key=lambda c: c.opened_on)
    if not cases:
        raise NotFoundError(f"候选人没有认证案件: {candidate_id}")
    case = cases[-1]
    version = service.case_version(case)

    decision = _decision_as_of(service, case, as_of)
    decision_view = None
    gaps_view = None
    if decision is not None:
        decision_view = {
            "decision_id": decision.decision_id,
            "outcome": decision.outcome.value,
            "decided_on": decision.decided_on.isoformat(),
            "framework": f"{decision.framework_id} v{decision.framework_revision}",
            "supersedes": decision.supersedes,
            "new_evidence_ids": list(decision.new_evidence_ids),
            "rationale": decision.rationale,
        }
        gaps_view = {
            dimension.value: {
                "score": round(decision.dimension_scores[dimension], 2),
                "required": round(version.dimension_requirement(dimension), 2),
                "gap": round(decision.dimension_gaps[dimension], 2),
            }
            for dimension in Dimension
        }

    calibration_view = []
    store = service.store()
    for session in store.collection("calibrations").values():
        if session.case_id != case.case_id or session.opened_on > as_of:
            continue
        calibration_view.append(
            {
                "type": "校准",
                "id": session.session_id,
                "round_no": session.round_no,
                "indicators": list(session.indicator_codes),
                "status": session.status.value,
                "opened_on": session.opened_on.isoformat(),
                "closed_on": session.closed_on.isoformat() if session.closed_on else None,
                "revisions": [
                    {
                        "assessor_id": r.assessor_id,
                        "indicator_code": r.indicator_code,
                        "new_value": r.new_value,
                    }
                    for r in session.revisions
                ],
            }
        )
    for review in store.collection("reviews").values():
        if review.case_id != case.case_id or review.opened_on > as_of:
            continue
        calibration_view.append(
            {
                "type": "复核",
                "id": review.review_id,
                "round_no": review.round_no,
                "indicators": list(review.indicator_codes),
                "status": review.status.value,
                "reviewer_id": review.reviewer_id,
                "final_scores": dict(review.final_scores),
                "opened_on": review.opened_on.isoformat(),
                "resolved_on": review.resolved_on.isoformat() if review.resolved_on else None,
            }
        )
    calibration_view.sort(key=lambda item: item["opened_on"])

    outstanding = []
    for plan in store.collection("plans").values():
        if plan.case_id != case.case_id:
            continue
        for step in plan.steps:
            if step.is_completed_as_of(as_of):
                continue
            outstanding.append(
                {
                    "step_id": step.step_id,
                    "plan_id": plan.plan_id,
                    "title": step.title,
                    "deadline": step.deadline.isoformat(),
                    "overdue": step.deadline < as_of,
                    "depends_on": list(step.depends_on),
                    "blocked": any(
                        not plan.step(dep).is_completed_as_of(as_of)
                        for dep in step.depends_on
                    ),
                }
            )

    return {
        "candidate_id": candidate.candidate_id,
        "candidate_name": candidate.name,
        "as_of": as_of.isoformat(),
        "case_id": case.case_id,
        "case_status": case.status.value,
        "decision": decision_view,
        "dimension_gaps": gaps_view,
        "calibration_process": calibration_view,
        "outstanding_steps": outstanding,
    }
