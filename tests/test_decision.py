from datetime import date

from helpers import ServiceTestCase, make_indicators
from teacher_certification.case import CaseStatus
from teacher_certification.decision import DecisionOutcome
from teacher_certification.errors import StateError, ValidationError
from teacher_certification.evidence import EvidenceKind
from teacher_certification.framework import Dimension


class DecisionOutcomeTests(ServiceTestCase):
    def _decide(self, cult_score):
        case_id = self.open_case()
        uniform = self.scores(80, **{"CULT-1": cult_score})
        self.drive_scoring(case_id, uniform, uniform, uniform)
        decision_id = self.service.record_decision(case_id, "评审组合议", self.today)
        return case_id, self.service.get_decision(decision_id)

    def test_pass_when_all_dimensions_meet_requirement(self):
        _, decision = self._decide(80)
        self.assertEqual(decision.outcome, DecisionOutcome.PASS)
        self.assertEqual(decision.dimension_gaps[Dimension.INTERCULTURAL], 10.0)

    def test_conditional_pass_within_margin(self):
        _, decision = self._decide(66)
        self.assertEqual(decision.outcome, DecisionOutcome.CONDITIONAL_PASS)
        self.assertAlmostEqual(decision.dimension_gaps[Dimension.INTERCULTURAL], -4.0)

    def test_reassessment_after_remediation_within_floor(self):
        _, decision = self._decide(60)
        self.assertEqual(decision.outcome, DecisionOutcome.REASSESS_AFTER_REMEDIATION)

    def test_fail_closes_case(self):
        case_id, decision = self._decide(40)
        self.assertEqual(decision.outcome, DecisionOutcome.FAIL)
        self.assertEqual(self.service.get_case(case_id).status, CaseStatus.CLOSED)

    def test_explicit_outcome_diverging_requires_rationale(self):
        case_id = self.open_case()
        self.drive_scoring(case_id, self.scores(80), self.scores(80), self.scores(80))
        with self.assertRaises(ValidationError):
            self.service.record_decision(
                case_id, "", self.today, outcome=DecisionOutcome.FAIL
            )


class FrameworkExpiryTests(ServiceTestCase):
    def test_expired_framework_cannot_issue_decision(self):
        framework_id = self.service.create_framework("短期框架")
        self.service.add_framework_version(
            framework_id, "上半年版", make_indicators(), date(2026, 1, 1), date(2026, 6, 30)
        )
        case_id = self.service.open_case(
            self.candidate, framework_id, [self.a1, self.a2, self.a3], self.today
        )
        self.drive_scoring(case_id, self.scores(80), self.scores(80), self.scores(80))
        with self.assertRaises(StateError):
            self.service.record_decision(case_id, "超期签发", date(2026, 7, 1))
        decision_id = self.service.record_decision(case_id, "期内签发", date(2026, 6, 30))
        self.assertIsNotNone(decision_id)


class ReassessmentTests(ServiceTestCase):
    def _remediation_decision(self):
        case_id = self.open_case()
        low = self.scores(80, **{"CULT-1": 60})
        self.drive_scoring(case_id, low, low, low)
        decision_id = self.service.record_decision(case_id, "跨文化短板明显", date(2026, 3, 10))
        plan_id = self.service.create_remediation_plan(
            decision_id,
            [
                {
                    "key": "s1",
                    "title": "跨文化工作坊",
                    "indicator_codes": ["CULT-1"],
                    "deadline": date(2026, 4, 1),
                }
            ],
            self.today,
        )
        return case_id, decision_id, plan_id

    def test_reassessment_flow_references_original_and_new_evidence(self):
        case_id, decision_id, plan_id = self._remediation_decision()
        plan = self.service.get_plan(plan_id)
        self.service.complete_remediation_step(
            plan.steps[0].step_id, self.mentor, date(2026, 3, 20)
        )
        self.service.reopen_for_reassessment(case_id, date(2026, 3, 21))
        new_evidence = self.service.submit_evidence(
            case_id, EvidenceKind.PRACTICE_FEEDBACK, ["CULT-1"], "导师丁", "工作坊反馈", date(2026, 3, 22)
        )
        high = self.scores(85)
        self.drive_scoring(case_id, high, high, high)
        with self.assertRaises(ValidationError):
            self.service.record_decision(case_id, "缺少原决定引用", date(2026, 3, 25))
        with self.assertRaises(ValidationError):
            self.service.record_decision(
                case_id, "缺少新增证据", date(2026, 3, 25), supersedes=decision_id
            )
        stale_evidence = self.service.submit_evidence(
            case_id, EvidenceKind.ACADEMIC, ["CULT-1"], "教务处", "旧材料", date(2026, 3, 5)
        )
        with self.assertRaises(ValidationError):
            self.service.record_decision(
                case_id,
                "证据早于原决定",
                date(2026, 3, 25),
                supersedes=decision_id,
                new_evidence_ids=[stale_evidence],
            )
        new_id = self.service.record_decision(
            case_id,
            "复评通过",
            date(2026, 3, 25),
            supersedes=decision_id,
            new_evidence_ids=[new_evidence],
        )
        decision = self.service.get_decision(new_id)
        self.assertEqual(decision.outcome, DecisionOutcome.PASS)
        self.assertEqual(decision.supersedes, decision_id)
        self.assertEqual(decision.new_evidence_ids, (new_evidence,))

    def test_reopen_requires_completed_plan(self):
        case_id, _, _ = self._remediation_decision()
        with self.assertRaises(StateError):
            self.service.reopen_for_reassessment(case_id, self.today)


if __name__ == "__main__":
    import unittest

    unittest.main()
