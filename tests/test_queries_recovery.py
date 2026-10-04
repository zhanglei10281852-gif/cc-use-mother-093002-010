from datetime import date

from helpers import ServiceTestCase
from teacher_certification.calibration import CalibrationStatus
from teacher_certification.case import CaseStatus
from teacher_certification.decision import DecisionOutcome
from teacher_certification.queries import secretary_briefing


class BriefingScenario(ServiceTestCase):
    """完整走一遍：偏离→校准→附条件通过→补强，供查询与恢复测试复用。"""

    def setUp(self):
        super().setUp()
        self.case_id = self.open_case()
        self.drive_scoring(
            self.case_id,
            self.scores(80, **{"LANG-1": 90, "CULT-1": 66}),
            self.scores(80, **{"LANG-1": 90, "CULT-1": 66}),
            self.scores(80, **{"LANG-1": 60, "CULT-1": 68}),
        )
        self.session_id = next(iter(self.service.store().collection("calibrations")))
        self.service.submit_calibration_revision(
            self.session_id, self.a3, {"LANG-1": 85.0}, date(2026, 3, 3)
        )
        self.service.resolve_calibration(self.session_id, "统一评分口径", date(2026, 3, 4))
        self.decision_id = self.service.record_decision(
            self.case_id, "跨文化素养略低于要求", date(2026, 3, 10)
        )
        self.plan_id = self.service.create_remediation_plan(
            self.decision_id,
            [
                {
                    "key": "workshop",
                    "title": "跨文化工作坊",
                    "indicator_codes": ["CULT-1"],
                    "deadline": date(2026, 4, 1),
                },
                {
                    "key": "demo",
                    "title": "试讲与反思",
                    "indicator_codes": ["CULT-1"],
                    "depends_on": ["workshop"],
                    "deadline": date(2026, 4, 15),
                },
            ],
            date(2026, 3, 10),
        )
        plan = self.service.get_plan(self.plan_id)
        self.step_workshop, self.step_demo = (s.step_id for s in plan.steps)
        self.service.complete_remediation_step(
            self.step_workshop, self.mentor, date(2026, 3, 20)
        )


class SecretaryBriefingTests(BriefingScenario):
    def test_briefing_returns_effective_decision_and_gaps(self):
        briefing = secretary_briefing(self.service, self.candidate, date(2026, 4, 10))
        self.assertEqual(briefing["decision"]["decision_id"], self.decision_id)
        self.assertEqual(briefing["decision"]["outcome"], DecisionOutcome.CONDITIONAL_PASS.value)
        gaps = briefing["dimension_gaps"]
        self.assertEqual(gaps["语言能力"]["required"], 70.0)
        self.assertLess(gaps["跨文化素养"]["gap"], 0)
        self.assertGreater(gaps["教学技能"]["gap"], 0)

    def test_briefing_is_point_in_time(self):
        before = secretary_briefing(self.service, self.candidate, date(2026, 3, 5))
        self.assertIsNone(before["decision"])
        self.assertEqual(len(before["calibration_process"]), 1)
        titles = {s["title"] for s in before["outstanding_steps"]}
        self.assertEqual(titles, {"跨文化工作坊", "试讲与反思"})

    def test_briefing_lists_calibration_process(self):
        briefing = secretary_briefing(self.service, self.candidate, date(2026, 4, 10))
        process = briefing["calibration_process"]
        self.assertEqual(process[0]["type"], "校准")
        self.assertEqual(process[0]["status"], "已校准")
        self.assertEqual(process[0]["revisions"][0]["new_value"], 85.0)

    def test_briefing_lists_outstanding_steps_with_overdue_flag(self):
        briefing = secretary_briefing(self.service, self.candidate, date(2026, 4, 10))
        outstanding = briefing["outstanding_steps"]
        self.assertEqual(len(outstanding), 1)
        self.assertEqual(outstanding[0]["step_id"], self.step_demo)
        self.assertFalse(outstanding[0]["overdue"])
        self.assertFalse(outstanding[0]["blocked"])
        later = secretary_briefing(self.service, self.candidate, date(2026, 4, 20))
        self.assertTrue(later["outstanding_steps"][0]["overdue"])


class RecoveryTests(ServiceTestCase):
    def test_pending_calibration_survives_restart(self):
        case_id = self.open_case()
        self.drive_scoring(
            case_id,
            self.scores(80, **{"LANG-1": 90}),
            self.scores(80, **{"LANG-1": 90}),
            self.scores(80, **{"LANG-1": 60}),
        )
        self.assertEqual(self.service.get_case(case_id).status, CaseStatus.CALIBRATION)
        self.reopen_service()
        case = self.service.get_case(case_id)
        self.assertEqual(case.status, CaseStatus.CALIBRATION)
        sessions = list(self.service.store().collection("calibrations").values())
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].status, CalibrationStatus.OPEN)
        self.assertEqual(sessions[0].indicator_codes, ("LANG-1",))
        # 恢复后流程可以继续推进
        self.service.submit_calibration_revision(
            sessions[0].session_id, self.a3, {"LANG-1": 85.0}, self.today
        )
        self.assertIsNone(self.service.resolve_calibration(sessions[0].session_id, "校准", self.today))
        self.assertEqual(self.service.get_case(case_id).status, CaseStatus.DECISION_READY)

    def test_ids_do_not_collide_after_restart(self):
        first = self.service.register_candidate("候选人甲")
        self.reopen_service()
        second = self.service.register_candidate("候选人乙")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    import unittest

    unittest.main()
