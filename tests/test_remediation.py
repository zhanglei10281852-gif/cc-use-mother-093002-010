from datetime import date

from helpers import ServiceTestCase
from teacher_certification.decision import DecisionOutcome
from teacher_certification.errors import NotFoundError, StateError, ValidationError


class RemediationTests(ServiceTestCase):
    def _conditional_decision(self):
        case_id = self.open_case()
        borderline = self.scores(80, **{"CULT-1": 66})
        self.drive_scoring(case_id, borderline, borderline, borderline)
        decision_id = self.service.record_decision(case_id, "附条件通过", self.today)
        return case_id, decision_id

    def _plan_steps(self):
        return [
            {
                "key": "base",
                "title": "跨文化交际工作坊",
                "indicator_codes": ["CULT-1"],
                "deadline": date(2026, 4, 1),
            },
            {
                "key": "practice",
                "title": "真实课堂试讲",
                "indicator_codes": ["CULT-1", "TEACH-2"],
                "depends_on": ["base"],
                "deadline": date(2026, 4, 20),
            },
            {
                "key": "report",
                "title": "反思报告",
                "indicator_codes": ["CULT-1"],
                "depends_on": ["practice"],
                "deadline": date(2026, 5, 10),
            },
        ]

    def test_plan_only_for_conditional_or_reassessment_outcomes(self):
        case_id = self.open_case()
        self.drive_scoring(case_id, self.scores(85), self.scores(85), self.scores(85))
        decision_id = self.service.record_decision(case_id, "通过", self.today)
        self.assertEqual(
            self.service.get_decision(decision_id).outcome, DecisionOutcome.PASS
        )
        with self.assertRaises(StateError):
            self.service.create_remediation_plan(decision_id, self._plan_steps(), self.today)

    def test_cyclic_dependencies_rejected(self):
        _, decision_id = self._conditional_decision()
        steps = [
            {"key": "a", "title": "甲", "depends_on": ["b"], "deadline": date(2026, 4, 1)},
            {"key": "b", "title": "乙", "depends_on": ["a"], "deadline": date(2026, 4, 2)},
        ]
        with self.assertRaises(ValidationError):
            self.service.create_remediation_plan(decision_id, steps, self.today)

    def test_unknown_dependency_rejected(self):
        _, decision_id = self._conditional_decision()
        steps = [
            {"key": "a", "title": "甲", "depends_on": ["ghost"], "deadline": date(2026, 4, 1)},
        ]
        with self.assertRaises(ValidationError):
            self.service.create_remediation_plan(decision_id, steps, self.today)

    def test_steps_complete_in_dependency_order_with_mentor(self):
        _, decision_id = self._conditional_decision()
        plan_id = self.service.create_remediation_plan(
            decision_id, self._plan_steps(), self.today
        )
        plan = self.service.get_plan(plan_id)
        base, practice, report = (s.step_id for s in plan.steps)
        with self.assertRaises(StateError):
            self.service.complete_remediation_step(practice, self.mentor, date(2026, 3, 10))
        with self.assertRaises(ValidationError):
            self.service.complete_remediation_step(base, self.a1, date(2026, 3, 10))
        self.service.complete_remediation_step(base, self.mentor, date(2026, 3, 10))
        self.assertFalse(self.service.get_plan(plan_id).is_complete())
        self.service.complete_remediation_step(practice, self.mentor, date(2026, 4, 2))
        self.service.complete_remediation_step(report, self.mentor, date(2026, 4, 25))
        self.assertTrue(self.service.get_plan(plan_id).is_complete())
        with self.assertRaises(StateError):
            self.service.complete_remediation_step(base, self.mentor, date(2026, 4, 26))

    def test_unknown_step_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.complete_remediation_step("PLAN-9999/S1", self.mentor, self.today)


if __name__ == "__main__":
    import unittest

    unittest.main()
