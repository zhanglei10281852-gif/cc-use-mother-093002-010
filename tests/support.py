"""测试公共支撑：构造标准框架、评委与案件。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from teacher_certification.model import (
    DecisionOutcome,
    Dimension,
    EvidenceKind,
    Indicator,
    RemediationStep,
    ScoreEntry,
)
from teacher_certification.service import CertificationService

FRAMEWORK_ID = "FW-ICT"
THRESHOLD = 3.0


def make_indicators():
    return (
        Indicator("L1", Dimension.LANGUAGE, "语言知识运用"),
        Indicator("L2", Dimension.LANGUAGE, "课堂语言示范"),
        Indicator("T1", Dimension.TEACHING, "教学设计"),
        Indicator("T2", Dimension.TEACHING, "课堂组织与互动"),
        Indicator("C1", Dimension.INTERCULTURAL, "跨文化沟通"),
    )


def make_thresholds():
    return {dim: THRESHOLD for dim in Dimension}


def make_service(repository=None, effective_to=date(2026, 12, 31)):
    service = CertificationService(repository)
    service.register_framework_version(
        FRAMEWORK_ID,
        1,
        "国际中文教师三维能力框架",
        make_indicators(),
        make_thresholds(),
        effective_from=date(2026, 1, 1),
        effective_to=effective_to,
    )
    service.publish_framework(FRAMEWORK_ID, 1)
    return service


def make_assessors(service):
    service.register_assessor(
        "A1", "评委甲", ["高级评审资质"], [Dimension.LANGUAGE, Dimension.TEACHING]
    )
    service.register_assessor(
        "A2", "评委乙", ["高级评审资质"], [Dimension.TEACHING, Dimension.INTERCULTURAL]
    )
    service.register_assessor(
        "A3", "评委丙", ["主任评审资质"], list(Dimension)
    )


def open_case_with_panel(service, case_id="CASE-1", candidate_id="CAND-1"):
    if candidate_id not in service.store.candidates:
        service.register_candidate(candidate_id, "候选人某")
    service.open_case(case_id, candidate_id, FRAMEWORK_ID, 1, date(2026, 2, 1))
    service.assign_panel(case_id, ["A1", "A2"])
    return case_id


def add_basic_evidence(service, case_id="CASE-1", prefix=""):
    """课堂观察、学术成果、实践反馈各一，归入对应指标。"""
    service.plan_observation_task(case_id, f"{prefix}TASK-1", "A1", date(2026, 2, 5), "课堂互动组织")
    service.complete_observation_task(
        f"{prefix}TASK-1", date(2026, 2, 10), "T2", "课堂互动组织混乱，学生参与度低", f"{prefix}EV-OBS"
    )
    service.record_evidence(
        case_id, f"{prefix}EV-ACAD", "L1", EvidenceKind.ACADEMIC_ACHIEVEMENT,
        "发表二语习得论文两篇", date(2026, 2, 8), "科研处",
    )
    service.record_evidence(
        case_id, f"{prefix}EV-PRAC", "C1", EvidenceKind.PRACTICE_FEEDBACK,
        "海外教学点反馈：跨文化沟通顺畅", date(2026, 2, 9), "教学点",
    )
    return service


def entries(**kwargs):
    """entries(L1=4, L2=4, T1=2, T2=2, C1=4) -> tuple[ScoreEntry, ...]"""
    return tuple(ScoreEntry(k, float(v)) for k, v in kwargs.items())


def drive_full_journey(service):
    """完整走一遍：立案→证据→评分(偏离)→校准→补强决定→计划→复评→通过发证。

    时间线：2026-02 立案评分，02-21 系统识别偏离进入待校准，02-26 校准完成，
    03-01 决定补强后复评，04-25 补强完成，05-02 复评通过并发证。
    """
    make_assessors(service)
    open_case_with_panel(service)
    add_basic_evidence(service)
    service.open_scoring("CASE-1", date(2026, 2, 15))
    service.submit_score_sheet(
        "CASE-1", "SH-A1", "A1", entries(L1=4, L2=4, T1=2, T2=2, C1=4), date(2026, 2, 20)
    )
    service.submit_score_sheet(
        "CASE-1", "SH-A2", "A2", entries(L1=4, L2=5, T1=2, T2=3, C1=2), date(2026, 2, 21)
    )
    service.open_calibration("CASE-1", "CAL-1", "A3", date(2026, 2, 22))
    service.submit_calibration_revision(
        "CASE-1", "SH-A2-R2", "A2", entries(L1=4, L2=5, T1=2, T2=3, C1=4), date(2026, 2, 23)
    )
    service.close_calibration("CASE-1", "CAL-1", date(2026, 2, 26), "乙评委修订跨文化评分")
    service.make_decision(
        "CASE-1",
        "D1",
        DecisionOutcome.REMEDIATION_THEN_REVIEW,
        "教学技能维度均分2.25低于合格线3.0：课堂观察显示互动组织薄弱，需补强后复评",
        date(2026, 3, 1),
    )
    service.create_remediation_plan(
        "CASE-1",
        "PLAN-1",
        (
            RemediationStep("S1", "观摩资深教师课堂并提交反思报告", (), date(2026, 4, 1), "M1"),
            RemediationStep("S2", "重做一次教学设计并试讲", ("S1",), date(2026, 5, 1), "M1"),
        ),
        date(2026, 3, 2),
    )
    service.submit_step_completion("PLAN-1", "S1", date(2026, 3, 10))
    service.confirm_step("PLAN-1", "S1", "M1", date(2026, 3, 12))
    service.submit_step_completion("PLAN-1", "S2", date(2026, 4, 20))
    service.confirm_step("PLAN-1", "S2", "M1", date(2026, 4, 25))
    service.record_evidence(
        "CASE-1", "EV-NEW", "T2", EvidenceKind.CLASSROOM_OBSERVATION,
        "复评试讲：课堂互动明显改善", date(2026, 4, 26), "复评观察组",
    )
    service.open_scoring("CASE-1", date(2026, 4, 27))
    service.submit_score_sheet(
        "CASE-1", "SH2-A1", "A1", entries(L1=4, L2=4, T1=4, T2=4, C1=4), date(2026, 4, 28)
    )
    service.submit_score_sheet(
        "CASE-1", "SH2-A2", "A2", entries(L1=4, L2=4, T1=4, T2=4, C1=4), date(2026, 4, 28)
    )
    service.make_decision(
        "CASE-1",
        "D2",
        DecisionOutcome.PASS,
        "复评三维均达合格线，同意通过",
        date(2026, 5, 2),
        supersedes_decision_id="D1",
        new_evidence_ids=("EV-NEW",),
    )
    return service
