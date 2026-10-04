from datetime import date

from helpers import ALL_DIMS, ServiceTestCase
from teacher_certification.case import CaseStatus
from teacher_certification.errors import NotFoundError, StateError, ValidationError
from teacher_certification.evidence import EvidenceKind
from teacher_certification.framework import Dimension


class EvidenceTests(ServiceTestCase):
    def test_evidence_must_map_to_known_indicator(self):
        case_id = self.open_case()
        with self.assertRaises(NotFoundError):
            self.service.submit_evidence(
                case_id, EvidenceKind.ACADEMIC, ["NOPE-1"], "教务处", "论文", self.today
            )

    def test_observation_task_produces_classroom_evidence(self):
        case_id = self.open_case()
        task_id = self.service.plan_observation_task(
            case_id, self.a1, ["TEACH-1", "TEACH-2"], date(2026, 3, 5)
        )
        evidence_id = self.service.complete_observation_task(task_id, "两次听课记录", self.today)
        evidence = self.service.store().collection("evidence")[evidence_id]
        self.assertEqual(evidence.kind, EvidenceKind.CLASSROOM_OBSERVATION)
        self.assertEqual(evidence.indicator_codes, ("TEACH-1", "TEACH-2"))
        self.assertEqual(evidence.task_id, task_id)

    def test_observation_requires_case_assessor(self):
        case_id = self.open_case()
        with self.assertRaises(ValidationError):
            self.service.plan_observation_task(
                case_id, self.mentor, ["TEACH-1"], date(2026, 3, 5)
            )


class ScoringTests(ServiceTestCase):
    def test_scores_require_prior_conflict_declaration(self):
        case_id = self.open_case()
        self.service.begin_scoring(case_id)
        with self.assertRaises(StateError):
            self.service.submit_scores(case_id, self.a1, self.scores(80), self.today)

    def test_scores_only_within_assessor_qualification(self):
        lang_only = self.service.register_assessor("语言评委", {Dimension.LANGUAGE})
        case_id = self.service.open_case(
            self.candidate, self.framework_id, [self.a1, self.a2, lang_only], self.today
        )
        self.service.begin_scoring(case_id)
        self.service.declare_conflict(case_id, lang_only, False, "", self.today)
        with self.assertRaises(ValidationError):
            self.service.submit_scores(
                case_id,
                lang_only,
                {"LANG-1": 85.0, "LANG-2": 88.0, "TEACH-1": 90.0},
                self.today,
            )
        sheet_id = self.service.submit_scores(
            case_id, lang_only, {"LANG-1": 85.0, "LANG-2": 88.0}, self.today
        )
        self.assertIsNotNone(sheet_id)

    def test_conflict_of_interest_excludes_sheet(self):
        case_id = self.open_case()
        self.service.begin_scoring(case_id)
        self.service.declare_conflict(case_id, self.a1, False, "", self.today)
        self.service.declare_conflict(case_id, self.a2, False, "", self.today)
        self.service.declare_conflict(case_id, self.a3, True, "与候选人为师生亲属", self.today)
        self.service.submit_scores(case_id, self.a1, self.scores(80), self.today)
        self.service.submit_scores(case_id, self.a2, self.scores(80), self.today)
        self.service.submit_scores(case_id, self.a3, self.scores(10), self.today)
        scores = self.service.compute_dimension_scores(case_id)
        self.assertEqual(scores[Dimension.LANGUAGE], 80.0)

    def test_conflict_declaration_requires_detail(self):
        case_id = self.open_case()
        with self.assertRaises(ValidationError):
            self.service.declare_conflict(case_id, self.a1, True, "", self.today)

    def test_sheets_are_sealed_while_scoring(self):
        case_id = self.open_case()
        self.service.begin_scoring(case_id)
        self.service.declare_conflict(case_id, self.a1, False, "", self.today)
        self.service.submit_scores(case_id, self.a1, self.scores(80), self.today)
        with self.assertRaises(StateError):
            self.service.submitted_scores(case_id)


class CalibrationTests(ServiceTestCase):
    def _open_calibration(self):
        case_id = self.open_case()
        self.drive_scoring(
            case_id,
            self.scores(80, **{"LANG-1": 90}),
            self.scores(80, **{"LANG-1": 90}),
            self.scores(80, **{"LANG-1": 60}),
        )
        return case_id

    def test_deviation_triggers_calibration(self):
        case_id = self._open_calibration()
        case = self.service.get_case(case_id)
        self.assertEqual(case.status, CaseStatus.CALIBRATION)
        sessions = list(self.service.store().collection("calibrations").values())
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].indicator_codes, ("LANG-1",))

    def test_calibration_revision_resolves_deviation(self):
        case_id = self._open_calibration()
        session_id = next(iter(self.service.store().collection("calibrations")))
        self.service.submit_calibration_revision(
            session_id, self.a3, {"LANG-1": 85.0}, self.today
        )
        review_id = self.service.resolve_calibration(session_id, "已沟通评分口径", self.today)
        self.assertIsNone(review_id)
        self.assertEqual(self.service.get_case(case_id).status, CaseStatus.DECISION_READY)
        scores = self.service.compute_dimension_scores(case_id)
        self.assertAlmostEqual(scores[Dimension.LANGUAGE], (90 + 90 + 85) / 6 + 80 / 2)

    def test_unresolved_calibration_escalates_to_review(self):
        case_id = self._open_calibration()
        session_id = next(iter(self.service.store().collection("calibrations")))
        self.service.submit_calibration_revision(
            session_id, self.a3, {"LANG-1": 70.0}, self.today
        )
        review_id = self.service.resolve_calibration(session_id, "仍存在分歧", self.today)
        self.assertIsNotNone(review_id)
        self.assertEqual(self.service.get_case(case_id).status, CaseStatus.REVIEW)
        with self.assertRaises(ValidationError):
            self.service.resolve_review(
                review_id, self.a1, {"LANG-1": 82.0}, "内部评委不能复核", self.today
            )
        external = self.service.register_assessor("校外专家", ALL_DIMS)
        self.service.resolve_review(review_id, external, {"LANG-1": 82.0}, "裁定", self.today)
        self.assertEqual(self.service.get_case(case_id).status, CaseStatus.DECISION_READY)
        scores = self.service.compute_dimension_scores(case_id)
        self.assertAlmostEqual(scores[Dimension.LANGUAGE], (82.0 + 80.0) / 2)

    def test_revision_limited_to_flagged_indicators(self):
        self._open_calibration()
        session_id = next(iter(self.service.store().collection("calibrations")))
        with self.assertRaises(ValidationError):
            self.service.submit_calibration_revision(
                session_id, self.a3, {"TEACH-1": 90.0}, self.today
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
