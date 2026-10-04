"""框架版本、证据归集、评委资质、独立评分、利益冲突、偏离校准与复核的规则测试。"""
import unittest
from datetime import date

from support import (
    FRAMEWORK_ID,
    add_basic_evidence,
    entries,
    make_assessors,
    make_indicators,
    make_service,
    make_thresholds,
    open_case_with_panel,
)

from teacher_certification.errors import (
    ConflictOfInterestError,
    FrameworkExpiredError,
    InvalidStateError,
    QualificationError,
    ValidationError,
)
from teacher_certification.model import (
    CalibrationStatus,
    CaseStatus,
    Dimension,
    EvidenceKind,
    FrameworkStatus,
    Indicator,
    ScoreEntry,
    TaskStatus,
)


class FrameworkVersionTests(unittest.TestCase):
    def test_framework_must_cover_three_dimensions(self):
        service = make_service()
        with self.assertRaises(ValidationError):
            service.register_framework_version(
                "FW-X", 1, "缺维度的框架",
                (Indicator("L1", Dimension.LANGUAGE, "语言知识"),),
                make_thresholds(),
                date(2026, 1, 1), None,
            )

    def test_duplicate_version_rejected(self):
        service = make_service()
        with self.assertRaises(ValidationError):
            service.register_framework_version(
                FRAMEWORK_ID, 1, "重复版本", make_indicators(), make_thresholds(),
                date(2026, 1, 1), None,
            )

    def test_invalid_validity_window_rejected(self):
        service = make_service()
        with self.assertRaises(ValidationError):
            service.register_framework_version(
                "FW-Y", 1, "窗口倒置", make_indicators(), make_thresholds(),
                date(2026, 6, 1), date(2026, 1, 1),
            )

    def test_versions_coexist_and_old_version_can_be_retired(self):
        service = make_service()
        service.register_framework_version(
            FRAMEWORK_ID, 2, "第二版框架", make_indicators(), make_thresholds(),
            date(2026, 7, 1), None,
        )
        service.publish_framework(FRAMEWORK_ID, 2)
        service.retire_framework(FRAMEWORK_ID, 1)
        self.assertEqual(
            service.get_framework(FRAMEWORK_ID, 1).status, FrameworkStatus.RETIRED
        )
        self.assertFalse(service.get_framework(FRAMEWORK_ID, 1).is_effective_on(date(2026, 8, 1)))
        self.assertTrue(service.get_framework(FRAMEWORK_ID, 2).is_effective_on(date(2026, 8, 1)))

    def test_open_case_on_ineffective_framework_rejected(self):
        service = make_service()
        make_assessors(service)
        service.register_candidate("CAND-1", "候选人某")
        with self.assertRaises(FrameworkExpiredError):
            service.open_case("CASE-0", "CAND-1", FRAMEWORK_ID, 1, date(2025, 12, 15))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        make_assessors(self.service)
        open_case_with_panel(self.service)

    def test_three_evidence_kinds_mapped_to_indicators(self):
        add_basic_evidence(self.service)
        evidence = self.service.case_evidence("CASE-1")
        self.assertEqual(len(evidence), 3)
        kinds = {e.kind for e in evidence}
        self.assertEqual(
            kinds,
            {
                EvidenceKind.CLASSROOM_OBSERVATION,
                EvidenceKind.ACADEMIC_ACHIEVEMENT,
                EvidenceKind.PRACTICE_FEEDBACK,
            },
        )
        mapping = {e.evidence_id: e.indicator_id for e in evidence}
        self.assertEqual(mapping, {"EV-OBS": "T2", "EV-ACAD": "L1", "EV-PRAC": "C1"})

    def test_observation_task_produces_evidence(self):
        add_basic_evidence(self.service)
        task = self.service.store.tasks["TASK-1"]
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.evidence_id, "EV-OBS")
        with self.assertRaises(InvalidStateError):
            self.service.complete_observation_task(
                "TASK-1", date(2026, 2, 11), "T1", "重复登记", "EV-DUP"
            )

    def test_evidence_must_map_to_framework_indicator(self):
        with self.assertRaises(ValidationError):
            self.service.record_evidence(
                "CASE-1", "EV-BAD", "NOPE", EvidenceKind.PRACTICE_FEEDBACK,
                "指标不存在", date(2026, 2, 8), "教学点",
            )

    def test_evidence_window_closed_after_scores_confirmed(self):
        add_basic_evidence(self.service)
        self.service.open_scoring("CASE-1", date(2026, 2, 15))
        sheet = entries(L1=4, L2=4, T1=4, T2=4, C1=4)
        self.service.submit_score_sheet("CASE-1", "SH-A1", "A1", sheet, date(2026, 2, 20))
        self.service.submit_score_sheet("CASE-1", "SH-A2", "A2", sheet, date(2026, 2, 20))
        self.assertEqual(self.service.get_case("CASE-1").status, CaseStatus.SCORES_CONFIRMED)
        with self.assertRaises(InvalidStateError):
            self.service.record_evidence(
                "CASE-1", "EV-LATE", "L1", EvidenceKind.ACADEMIC_ACHIEVEMENT,
                "迟到的证据", date(2026, 2, 21), "科研处",
            )


class PanelAndScoringTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        make_assessors(self.service)

    def test_panel_needs_two_assessors(self):
        open_case_with_panel(self.service)
        with self.assertRaises(ValidationError):
            self.service.assign_panel("CASE-1", ["A1"])

    def test_inactive_assessor_cannot_join_panel(self):
        self.service.deactivate_assessor("A2")
        self.service.register_candidate("CAND-9", "候选人九")
        self.service.open_case("CASE-9", "CAND-9", FRAMEWORK_ID, 1, date(2026, 2, 1))
        with self.assertRaises(QualificationError):
            self.service.assign_panel("CASE-9", ["A1", "A2"])

    def test_panel_must_cover_all_dimensions(self):
        self.service.register_assessor("A8", "评委八", ["评审资质"], [Dimension.LANGUAGE])
        self.service.register_assessor("A9", "评委九", ["评审资质"], [Dimension.LANGUAGE])
        open_case_with_panel(self.service)
        with self.assertRaises(QualificationError):
            self.service.assign_panel("CASE-1", ["A8", "A9"])

    def test_scoring_requires_evidence_first(self):
        open_case_with_panel(self.service)
        with self.assertRaises(ValidationError):
            self.service.open_scoring("CASE-1", date(2026, 2, 15))

    def test_sheet_must_cover_all_indicators_in_range(self):
        open_case_with_panel(self.service)
        add_basic_evidence(self.service)
        self.service.open_scoring("CASE-1", date(2026, 2, 15))
        with self.assertRaises(ValidationError):
            self.service.submit_score_sheet(
                "CASE-1", "SH-PART", "A1", entries(L1=4, L2=4, T1=4), date(2026, 2, 20)
            )
        with self.assertRaises(ValidationError):
            self.service.submit_score_sheet(
                "CASE-1", "SH-RANGE", "A1",
                entries(L1=4, L2=4, T1=4, T2=4, C1=9), date(2026, 2, 20),
            )

    def test_independent_scoring_rules(self):
        open_case_with_panel(self.service)
        add_basic_evidence(self.service)
        self.service.open_scoring("CASE-1", date(2026, 2, 15))
        sheet = entries(L1=4, L2=4, T1=4, T2=4, C1=4)
        with self.assertRaises(ValidationError):
            # A3 不在评审组
            self.service.submit_score_sheet("CASE-1", "SH-A3", "A3", sheet, date(2026, 2, 20))
        self.service.submit_score_sheet("CASE-1", "SH-A1", "A1", sheet, date(2026, 2, 20))
        with self.assertRaises(ValidationError):
            # 同轮重复提交
            self.service.submit_score_sheet("CASE-1", "SH-A1B", "A1", sheet, date(2026, 2, 20))


class ConflictOfInterestTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        make_assessors(self.service)
        open_case_with_panel(self.service)
        add_basic_evidence(self.service)
        self.service.open_scoring("CASE-1", date(2026, 2, 15))

    def test_recused_assessor_excluded_from_scoring(self):
        self.service.declare_conflict(
            "CASE-1", "A2", True, "与候选人为同一课题组成员", date(2026, 2, 16)
        )
        with self.assertRaises(ConflictOfInterestError):
            self.service.submit_score_sheet(
                "CASE-1", "SH-A2", "A2",
                entries(L1=4, L2=4, T1=4, T2=4, C1=4), date(2026, 2, 20),
            )
        self.service.submit_score_sheet(
            "CASE-1", "SH-A1", "A1", entries(L1=3, L2=3, T1=3, T2=3, C1=3), date(2026, 2, 20)
        )
        case = self.service.get_case("CASE-1")
        # 仅剩一名未回避评委，无偏离可比，直接确认
        self.assertEqual(case.status, CaseStatus.SCORES_CONFIRMED)
        gaps = {g.dimension: g for g in self.service.dimension_gaps("CASE-1")}
        self.assertAlmostEqual(gaps[Dimension.LANGUAGE].average, 3.0)

    def test_conflict_declaration_requires_reason_and_is_unique(self):
        with self.assertRaises(ValidationError):
            self.service.declare_conflict("CASE-1", "A2", True, "", date(2026, 2, 16))
        self.service.declare_conflict("CASE-1", "A2", False, "", date(2026, 2, 16))
        with self.assertRaises(ValidationError):
            self.service.declare_conflict("CASE-1", "A2", True, "重复申报", date(2026, 2, 17))


class DeviationCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        make_assessors(self.service)
        open_case_with_panel(self.service)
        add_basic_evidence(self.service)
        self.service.open_scoring("CASE-1", date(2026, 2, 15))

    def test_deviation_triggers_calibration_then_resolves(self):
        self.service.submit_score_sheet(
            "CASE-1", "SH-A1", "A1", entries(L1=4, L2=4, T1=2, T2=2, C1=4), date(2026, 2, 20)
        )
        self.service.submit_score_sheet(
            "CASE-1", "SH-A2", "A2", entries(L1=4, L2=5, T1=2, T2=3, C1=2), date(2026, 2, 21)
        )
        case = self.service.get_case("CASE-1")
        self.assertEqual(case.status, CaseStatus.CALIBRATION_PENDING)
        self.assertEqual(len(case.pending_deviation), 1)
        flag = case.pending_deviation[0]
        self.assertEqual(flag.indicator_id, "C1")
        self.assertEqual(flag.spread, 2.0)
        self.assertEqual(flag.scores, {"A1": 4.0, "A2": 2.0})

        session = self.service.open_calibration("CASE-1", "CAL-1", "A3", date(2026, 2, 22))
        self.assertEqual(session.trigger[0].indicator_id, "C1")
        revision = self.service.submit_calibration_revision(
            "CASE-1", "SH-A2-R2", "A2",
            entries(L1=4, L2=5, T1=2, T2=3, C1=4), date(2026, 2, 23),
        )
        self.assertEqual(revision.revision, 2)
        session = self.service.close_calibration("CASE-1", "CAL-1", date(2026, 2, 26), "已对齐")
        self.assertEqual(session.status, CalibrationStatus.CLOSED_RESOLVED)
        self.assertEqual(self.service.get_case("CASE-1").status, CaseStatus.SCORES_CONFIRMED)

        gaps = {g.dimension: g for g in self.service.dimension_gaps("CASE-1")}
        self.assertAlmostEqual(gaps[Dimension.TEACHING].average, 2.25)
        self.assertFalse(gaps[Dimension.TEACHING].meets_threshold)
        self.assertAlmostEqual(gaps[Dimension.INTERCULTURAL].average, 4.0)

    def test_unresolved_deviation_escalates_to_review(self):
        self.service.submit_score_sheet(
            "CASE-1", "SH-A1", "A1", entries(L1=4, L2=4, T1=4, T2=4, C1=5), date(2026, 2, 20)
        )
        self.service.submit_score_sheet(
            "CASE-1", "SH-A2", "A2", entries(L1=4, L2=4, T1=4, T2=4, C1=2), date(2026, 2, 21)
        )
        self.service.open_calibration("CASE-1", "CAL-1", "A3", date(2026, 2, 22))
        self.service.submit_calibration_revision(
            "CASE-1", "SH-A2-R2", "A2",
            entries(L1=4, L2=4, T1=4, T2=4, C1=3), date(2026, 2, 23),
        )
        session = self.service.close_calibration("CASE-1", "CAL-1", date(2026, 2, 24), "仍有分歧")
        self.assertEqual(session.status, CalibrationStatus.ESCALATED_TO_REVIEW)
        self.assertEqual(self.service.get_case("CASE-1").status, CaseStatus.REVIEW_PENDING)

        with self.assertRaises(ValidationError):
            # 裁定未覆盖争议指标 C1
            self.service.submit_review_resolution(
                "CASE-1", "SH-REV", "A3", (ScoreEntry("L1", 4.0),), date(2026, 2, 25)
            )
        self.service.submit_review_resolution(
            "CASE-1", "SH-REV", "A3", (ScoreEntry("C1", 4.0),), date(2026, 2, 25)
        )
        self.assertEqual(self.service.get_case("CASE-1").status, CaseStatus.SCORES_CONFIRMED)
        gaps = {g.dimension: g for g in self.service.dimension_gaps("CASE-1")}
        # 复核裁定覆盖原评分
        self.assertAlmostEqual(gaps[Dimension.INTERCULTURAL].average, 4.0)


if __name__ == "__main__":
    unittest.main()
