"""补强计划（依赖/期限/导师确认）、评审秘书 as-of 快照、服务恢复状态保持的测试。"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from support import (
    add_basic_evidence,
    drive_full_journey,
    entries,
    make_assessors,
    make_service,
    open_case_with_panel,
)

from teacher_certification.errors import (
    DependencyError,
    InvalidStateError,
    StepOverdueError,
    ValidationError,
)
from teacher_certification.model import (
    CalibrationStatus,
    CaseStatus,
    CertificateStatus,
    DecisionOutcome,
    Dimension,
    RemediationStep,
    StepStatus,
)
from teacher_certification.repository import JsonFileRepository
from teacher_certification.service import CertificationService


def service_with_remediation_decision():
    """走到「补强后复评」决定的服务。"""
    service = make_service()
    make_assessors(service)
    open_case_with_panel(service)
    add_basic_evidence(service)
    service.open_scoring("CASE-1", date(2026, 2, 15))
    sheet = entries(L1=4, L2=4, T1=2, T2=2, C1=4)
    service.submit_score_sheet("CASE-1", "SH-A1", "A1", sheet, date(2026, 2, 20))
    service.submit_score_sheet("CASE-1", "SH-A2", "A2", sheet, date(2026, 2, 20))
    service.make_decision(
        "CASE-1", "D1", DecisionOutcome.REMEDIATION_THEN_REVIEW,
        "教学技能短板，补强后复评", date(2026, 3, 1),
    )
    return service


def two_step_plan():
    return (
        RemediationStep("S1", "观摩资深教师课堂", (), date(2026, 4, 1), "M1"),
        RemediationStep("S2", "重做教学设计并试讲", ("S1",), date(2026, 5, 1), "M1"),
    )


class RemediationPlanTests(unittest.TestCase):
    def setUp(self):
        self.service = service_with_remediation_decision()

    def test_plan_requires_remediation_decision(self):
        service = make_service()
        make_assessors(service)
        open_case_with_panel(service)
        add_basic_evidence(service)
        service.open_scoring("CASE-1", date(2026, 2, 15))
        sheet = entries(L1=4, L2=4, T1=4, T2=4, C1=4)
        service.submit_score_sheet("CASE-1", "SH-A1", "A1", sheet, date(2026, 2, 20))
        service.submit_score_sheet("CASE-1", "SH-A2", "A2", sheet, date(2026, 2, 20))
        service.make_decision("CASE-1", "D1", DecisionOutcome.FAIL, "综合不达标", date(2026, 3, 1))
        with self.assertRaises(InvalidStateError):
            service.create_remediation_plan("CASE-1", "P-X", two_step_plan(), date(2026, 3, 2))

    def test_plan_validation(self):
        with self.assertRaises(ValidationError):
            # 循环依赖
            self.service.create_remediation_plan(
                "CASE-1", "P-CYC",
                (
                    RemediationStep("S1", "甲", ("S2",), date(2026, 4, 1), "M1"),
                    RemediationStep("S2", "乙", ("S1",), date(2026, 4, 1), "M1"),
                ),
                date(2026, 3, 2),
            )
        with self.assertRaises(ValidationError):
            # 未知依赖
            self.service.create_remediation_plan(
                "CASE-1", "P-UNK",
                (RemediationStep("S1", "甲", ("S9",), date(2026, 4, 1), "M1"),),
                date(2026, 3, 2),
            )
        with self.assertRaises(ValidationError):
            # 期限早于创建日
            self.service.create_remediation_plan(
                "CASE-1", "P-DUE",
                (RemediationStep("S1", "甲", (), date(2026, 2, 1), "M1"),),
                date(2026, 3, 2),
            )
        with self.assertRaises(ValidationError):
            # 缺少导师
            self.service.create_remediation_plan(
                "CASE-1", "P-MEN",
                (RemediationStep("S1", "甲", (), date(2026, 4, 1), ""),),
                date(2026, 3, 2),
            )

    def test_dependency_order_enforced(self):
        self.service.create_remediation_plan("CASE-1", "PLAN-1", two_step_plan(), date(2026, 3, 2))
        with self.assertRaises(DependencyError):
            self.service.submit_step_completion("PLAN-1", "S2", date(2026, 3, 10))
        self.service.submit_step_completion("PLAN-1", "S1", date(2026, 3, 10))
        with self.assertRaises(DependencyError):
            # S1 已提交但未经导师确认
            self.service.submit_step_completion("PLAN-1", "S2", date(2026, 3, 11))
        self.service.confirm_step("PLAN-1", "S1", "M1", date(2026, 3, 12))
        self.service.submit_step_completion("PLAN-1", "S2", date(2026, 4, 20))

    def test_deadline_enforced(self):
        self.service.create_remediation_plan("CASE-1", "PLAN-1", two_step_plan(), date(2026, 3, 2))
        with self.assertRaises(StepOverdueError):
            self.service.submit_step_completion("PLAN-1", "S1", date(2026, 4, 2))

    def test_mentor_confirmation_required(self):
        self.service.create_remediation_plan("CASE-1", "PLAN-1", two_step_plan(), date(2026, 3, 2))
        with self.assertRaises(InvalidStateError):
            self.service.confirm_step("PLAN-1", "S1", "M1", date(2026, 3, 10))
        self.service.submit_step_completion("PLAN-1", "S1", date(2026, 3, 10))
        with self.assertRaises(ValidationError):
            self.service.confirm_step("PLAN-1", "S1", "M2", date(2026, 3, 12))
        plan = self.service.confirm_step("PLAN-1", "S1", "M1", date(2026, 3, 12))
        self.assertEqual(plan.step("S1").status, StepStatus.CONFIRMED)
        self.assertEqual(plan.step("S1").confirmed_by, "M1")

    def test_fulfilled_plan_moves_case_to_re_review(self):
        self.service.create_remediation_plan("CASE-1", "PLAN-1", two_step_plan(), date(2026, 3, 2))
        self.assertEqual(self.service.get_case("CASE-1").status, CaseStatus.REMEDIATION)
        self.service.submit_step_completion("PLAN-1", "S1", date(2026, 3, 10))
        self.service.confirm_step("PLAN-1", "S1", "M1", date(2026, 3, 12))
        self.service.submit_step_completion("PLAN-1", "S2", date(2026, 4, 20))
        self.service.confirm_step("PLAN-1", "S2", "M1", date(2026, 4, 25))
        self.assertTrue(self.service.get_plan("PLAN-1").fulfilled)
        self.assertEqual(self.service.get_case("CASE-1").status, CaseStatus.RE_REVIEW)


class SecretarySnapshotTests(unittest.TestCase):
    """评审秘书：给定候选人与查询日期，获得当时有效的认证全貌。"""

    @classmethod
    def setUpClass(cls):
        cls.service = drive_full_journey(make_service())

    def test_snapshot_during_remediation(self):
        snap = self.service.certification_snapshot("CAND-1", date(2026, 3, 15))
        self.assertEqual(snap.case_id, "CASE-1")
        self.assertEqual(snap.case_status, CaseStatus.REMEDIATION)
        # 当时有效的决定：补强后复评，理由说明延期原因
        self.assertIsNotNone(snap.effective_decision)
        self.assertEqual(snap.effective_decision.decision_id, "D1")
        self.assertEqual(snap.effective_decision.outcome, DecisionOutcome.REMEDIATION_THEN_REVIEW)
        self.assertIn("教学技能", snap.effective_decision.rationale)
        # 三维差距：教学技能为负
        gaps = {g.dimension: g for g in snap.dimension_gaps}
        self.assertAlmostEqual(gaps[Dimension.TEACHING].average, 2.25)
        self.assertAlmostEqual(gaps[Dimension.TEACHING].gap, -0.75)
        self.assertFalse(gaps[Dimension.TEACHING].meets_threshold)
        self.assertTrue(gaps[Dimension.LANGUAGE].meets_threshold)
        self.assertTrue(gaps[Dimension.INTERCULTURAL].meets_threshold)
        # 评委校准过程可见
        self.assertEqual(len(snap.calibration_trail), 1)
        session = snap.calibration_trail[0]
        self.assertEqual(session.status, CalibrationStatus.CLOSED_RESOLVED)
        self.assertEqual(session.trigger[0].indicator_id, "C1")
        # 尚未完成的补强步骤：S1 已确认，S2 未完成
        self.assertEqual([s.step_id for s in snap.outstanding_steps], ["S2"])
        self.assertIsNone(snap.certificate)

    def test_snapshot_after_pass(self):
        snap = self.service.certification_snapshot("CAND-1", date(2026, 5, 3))
        self.assertEqual(snap.case_status, CaseStatus.CLOSED)
        self.assertEqual(snap.effective_decision.decision_id, "D2")
        self.assertEqual(snap.effective_decision.outcome, DecisionOutcome.PASS)
        self.assertEqual(snap.effective_decision.supersedes_decision_id, "D1")
        self.assertEqual(snap.effective_decision.new_evidence_ids, ("EV-NEW",))
        self.assertTrue(all(g.meets_threshold for g in snap.dimension_gaps))
        self.assertEqual(snap.outstanding_steps, ())
        self.assertIsNotNone(snap.certificate)
        self.assertEqual(snap.certificate.status, CertificateStatus.VALID)

    def test_snapshot_mid_calibration(self):
        snap = self.service.certification_snapshot("CAND-1", date(2026, 2, 25))
        self.assertEqual(snap.case_status, CaseStatus.CALIBRATION_PENDING)
        self.assertIsNone(snap.effective_decision)
        self.assertEqual(len(snap.calibration_trail), 1)
        self.assertEqual(snap.calibration_trail[0].status, CalibrationStatus.OPEN)

    def test_snapshot_before_any_case(self):
        snap = self.service.certification_snapshot("CAND-1", date(2026, 1, 15))
        self.assertIsNone(snap.case_id)
        self.assertIsNone(snap.effective_decision)


class PersistenceTests(unittest.TestCase):
    def test_pending_calibration_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.json"
            service = make_service(JsonFileRepository(path))
            make_assessors(service)
            open_case_with_panel(service)
            add_basic_evidence(service)
            service.open_scoring("CASE-1", date(2026, 2, 15))
            service.submit_score_sheet(
                "CASE-1", "SH-A1", "A1",
                entries(L1=4, L2=4, T1=2, T2=2, C1=4), date(2026, 2, 20),
            )
            service.submit_score_sheet(
                "CASE-1", "SH-A2", "A2",
                entries(L1=4, L2=5, T1=2, T2=3, C1=2), date(2026, 2, 21),
            )
            self.assertEqual(
                service.get_case("CASE-1").status, CaseStatus.CALIBRATION_PENDING
            )

            # 服务恢复：新实例从同一文件加载
            restored = CertificationService(JsonFileRepository(path))
            case = restored.get_case("CASE-1")
            self.assertEqual(case.status, CaseStatus.CALIBRATION_PENDING)
            self.assertEqual([f.indicator_id for f in case.pending_deviation], ["C1"])
            self.assertEqual(case.pending_deviation[0].scores, {"A1": 4.0, "A2": 2.0})

            # 恢复后流程可继续推进
            restored.open_calibration("CASE-1", "CAL-1", "A3", date(2026, 2, 22))
            restored.submit_calibration_revision(
                "CASE-1", "SH-A2-R2", "A2",
                entries(L1=4, L2=5, T1=2, T2=3, C1=4), date(2026, 2, 23),
            )
            restored.close_calibration("CASE-1", "CAL-1", date(2026, 2, 26))
            self.assertEqual(
                restored.get_case("CASE-1").status, CaseStatus.SCORES_CONFIRMED
            )

            # 再次恢复，状态依然保持
            again = CertificationService(JsonFileRepository(path))
            self.assertEqual(
                again.get_case("CASE-1").status, CaseStatus.SCORES_CONFIRMED
            )
            snap = again.certification_snapshot("CAND-1", date(2026, 3, 1))
            self.assertEqual(len(snap.calibration_trail), 1)

    def test_full_journey_state_roundtrips_through_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.json"
            drive_full_journey(make_service(JsonFileRepository(path)))
            restored = CertificationService(JsonFileRepository(path))
            case = restored.get_case("CASE-1")
            self.assertEqual(case.status, CaseStatus.CLOSED)
            self.assertEqual(case.decision_ids, ("D1", "D2"))
            cert = restored.valid_certificate_for("CAND-1")
            self.assertIsNotNone(cert)
            self.assertEqual(cert.decision_id, "D2")
            plan = restored.get_plan("PLAN-1")
            self.assertTrue(plan.fulfilled)
            self.assertEqual(plan.step("S2").depends_on, ("S1",))


if __name__ == "__main__":
    unittest.main()
