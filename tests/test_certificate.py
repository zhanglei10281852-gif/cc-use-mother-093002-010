import threading
from datetime import date

from helpers import ServiceTestCase, make_indicators
from teacher_certification.certificate import CertificateStatus
from teacher_certification.errors import ConcurrencyError, StateError


class CertificateTests(ServiceTestCase):
    def _passing_case(self):
        case_id = self.open_case()
        self.drive_scoring(case_id, self.scores(85), self.scores(85), self.scores(85))
        decision_id = self.service.record_decision(case_id, "通过", self.today)
        return case_id, decision_id

    def test_certificate_issued_on_pass(self):
        self._passing_case()
        certificate_id = self.service.issue_certificate(self.candidate, self.today)
        certificate = self.service.store().collection("certificates")[certificate_id]
        self.assertEqual(certificate.status, CertificateStatus.ACTIVE)
        self.assertEqual(self.service.get_case(certificate.case_id).status.value, "已结案")

    def test_second_certificate_rejected(self):
        self._passing_case()
        self.service.issue_certificate(self.candidate, self.today)
        with self.assertRaises(ConcurrencyError):
            self.service.issue_certificate(self.candidate, self.today)

    def test_conditional_pass_requires_completed_plan_before_issue(self):
        case_id = self.open_case()
        borderline = self.scores(80, **{"CULT-1": 66})
        self.drive_scoring(case_id, borderline, borderline, borderline)
        decision_id = self.service.record_decision(case_id, "附条件通过", self.today)
        plan_id = self.service.create_remediation_plan(
            decision_id,
            [{"key": "s", "title": "补强", "deadline": date(2026, 4, 1)}],
            self.today,
        )
        with self.assertRaises(StateError):
            self.service.issue_certificate(self.candidate, self.today)
        step = self.service.get_plan(plan_id).steps[0]
        self.service.complete_remediation_step(step.step_id, self.mentor, date(2026, 3, 15))
        certificate_id = self.service.issue_certificate(self.candidate, date(2026, 3, 16))
        self.assertIsNotNone(certificate_id)

    def test_expired_framework_blocks_certificate(self):
        framework_id = self.service.create_framework("短期框架")
        self.service.add_framework_version(
            framework_id, "上半年版", make_indicators(), date(2026, 1, 1), date(2026, 6, 30)
        )
        case_id = self.service.open_case(
            self.candidate, framework_id, [self.a1, self.a2, self.a3], self.today
        )
        self.drive_scoring(case_id, self.scores(85), self.scores(85), self.scores(85))
        self.service.record_decision(case_id, "通过", date(2026, 6, 30))
        with self.assertRaises(StateError):
            self.service.issue_certificate(self.candidate, date(2026, 7, 1))

    def test_concurrent_issue_yields_single_active_certificate(self):
        self._passing_case()
        barrier = threading.Barrier(8)
        outcomes = []
        lock = threading.Lock()

        def attempt():
            barrier.wait(timeout=5)
            try:
                result = self.service.issue_certificate(self.candidate, self.today)
                with lock:
                    outcomes.append(("ok", result))
            except ConcurrencyError:
                with lock:
                    outcomes.append(("conflict", None))

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        successes = [o for o in outcomes if o[0] == "ok"]
        self.assertEqual(len(successes), 1, f"并发签发结果异常: {outcomes}")
        self.assertEqual(len(outcomes), 8)
        active = [
            c
            for c in self.service.store().collection("certificates").values()
            if c.candidate_id == self.candidate and c.status is CertificateStatus.ACTIVE
        ]
        self.assertEqual(len(active), 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
