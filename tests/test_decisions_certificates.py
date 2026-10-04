"""认证决定（四种结论）、复评引用规则、证书唯一性与并发保护的测试。"""
import threading
import unittest
from datetime import date

from support import (
    FRAMEWORK_ID,
    add_basic_evidence,
    drive_full_journey,
    entries,
    make_assessors,
    make_service,
    open_case_with_panel,
)

from teacher_certification.errors import (
    DecisionError,
    DuplicateCertificateError,
    FrameworkExpiredError,
    InvalidStateError,
)
from teacher_certification.model import (
    CaseStatus,
    CertificateStatus,
    DecisionOutcome,
    Dimension,
    EvidenceKind,
    RemediationStep,
)


def drive_to_confirmed(service, case_id="CASE-1", candidate_id="CAND-1", score=4, prefix=""):
    """快速走到评分已确认：无偏离的同分评分。"""
    if "A1" not in service.store.assessors:
        make_assessors(service)
    open_case_with_panel(service, case_id=case_id, candidate_id=candidate_id)
    add_basic_evidence(service, case_id=case_id, prefix=prefix)
    service.open_scoring(case_id, date(2026, 2, 15))
    sheet = entries(L1=score, L2=score, T1=score, T2=score, C1=score)
    service.submit_score_sheet(case_id, f"{prefix}SH-A1", "A1", sheet, date(2026, 2, 20))
    service.submit_score_sheet(case_id, f"{prefix}SH-A2", "A2", sheet, date(2026, 2, 20))
    return service


class DecisionOutcomeTests(unittest.TestCase):
    def test_pass_blocked_when_any_dimension_short(self):
        """单项很高但真实课堂暴露短板：不能凭总分通过。"""
        service = make_service()
        make_assessors(service)
        open_case_with_panel(service)
        add_basic_evidence(service)
        service.open_scoring("CASE-1", date(2026, 2, 15))
        # 语言、跨文化很高，教学技能明显短板
        sheet_a1 = entries(L1=5, L2=5, T1=2, T2=2, C1=5)
        sheet_a2 = entries(L1=5, L2=5, T1=2, T2=2, C1=5)
        service.submit_score_sheet("CASE-1", "SH-A1", "A1", sheet_a1, date(2026, 2, 20))
        service.submit_score_sheet("CASE-1", "SH-A2", "A2", sheet_a2, date(2026, 2, 20))
        with self.assertRaises(DecisionError) as ctx:
            service.make_decision(
                "CASE-1", "D1", DecisionOutcome.PASS, "试图直接通过", date(2026, 3, 1)
            )
        self.assertIn("教学技能", str(ctx.exception))
        # 补强后复评才是合法结论，且必须说明理由
        decision = service.make_decision(
            "CASE-1", "D1", DecisionOutcome.REMEDIATION_THEN_REVIEW,
            "教学技能均分2.0低于合格线3.0，课堂互动组织薄弱，需补强后复评",
            date(2026, 3, 1),
        )
        self.assertEqual(set(decision.dimension_averages), set(Dimension))
        self.assertAlmostEqual(decision.dimension_averages[Dimension.TEACHING], 2.0)
        self.assertEqual(service.get_case("CASE-1").status, CaseStatus.DECIDED)

    def test_decision_requires_rationale(self):
        service = drive_to_confirmed(make_service())
        with self.assertRaises(DecisionError):
            service.make_decision("CASE-1", "D1", DecisionOutcome.FAIL, "", date(2026, 3, 1))

    def test_first_decision_cannot_reference_original(self):
        service = drive_to_confirmed(make_service())
        with self.assertRaises(DecisionError):
            service.make_decision(
                "CASE-1", "D1", DecisionOutcome.PASS, "非法引用",
                date(2026, 3, 1), supersedes_decision_id="D0",
            )

    def test_conditional_pass_requires_conditions(self):
        service = drive_to_confirmed(make_service())
        with self.assertRaises(DecisionError):
            service.make_decision(
                "CASE-1", "D1", DecisionOutcome.CONDITIONAL_PASS, "附条件通过",
                date(2026, 3, 1),
            )
        service.make_decision(
            "CASE-1", "D1", DecisionOutcome.CONDITIONAL_PASS,
            "跨文化维度勉强达标，须在一学期内完成海外观摩",
            date(2026, 3, 1),
            conditions="完成海外教学观摩并提交报告",
            conditions_due=date(2026, 7, 1),
        )
        case = service.begin_re_review("CASE-1", date(2026, 6, 1))
        self.assertEqual(case.status, CaseStatus.RE_REVIEW)

    def test_expired_framework_cannot_issue_decision(self):
        service = make_service(effective_to=date(2026, 6, 30))
        drive_to_confirmed(service)
        with self.assertRaises(FrameworkExpiredError):
            service.make_decision(
                "CASE-1", "D1", DecisionOutcome.PASS, "过期签发", date(2026, 7, 1)
            )
        # 有效期内（含边界日）可以签发
        service.make_decision("CASE-1", "D1", DecisionOutcome.PASS, "三维均达标", date(2026, 6, 30))
        self.assertEqual(service.get_case("CASE-1").status, CaseStatus.CLOSED)

    def test_retired_framework_cannot_issue_decision(self):
        service = make_service()
        drive_to_confirmed(service)
        service.retire_framework(FRAMEWORK_ID, 1)
        with self.assertRaises(FrameworkExpiredError):
            service.make_decision(
                "CASE-1", "D1", DecisionOutcome.PASS, "停用框架", date(2026, 3, 1)
            )


class ReReviewTests(unittest.TestCase):
    def setUp(self):
        """走到补强完成、待复评的状态。"""
        self.service = make_service()
        make_assessors(self.service)
        open_case_with_panel(self.service)
        add_basic_evidence(self.service)
        self.service.open_scoring("CASE-1", date(2026, 2, 15))
        sheet = entries(L1=4, L2=4, T1=2, T2=2, C1=4)
        self.service.submit_score_sheet("CASE-1", "SH-A1", "A1", sheet, date(2026, 2, 20))
        self.service.submit_score_sheet("CASE-1", "SH-A2", "A2", sheet, date(2026, 2, 20))
        self.service.make_decision(
            "CASE-1", "D1", DecisionOutcome.REMEDIATION_THEN_REVIEW,
            "教学技能短板，补强后复评", date(2026, 3, 1),
        )
        self.service.create_remediation_plan(
            "CASE-1", "PLAN-1",
            (RemediationStep("S1", "观摩资深教师课堂", (), date(2026, 4, 1), "M1"),),
            date(2026, 3, 2),
        )
        self.service.submit_step_completion("PLAN-1", "S1", date(2026, 3, 10))
        self.service.confirm_step("PLAN-1", "S1", "M1", date(2026, 3, 12))
        self.assertEqual(self.service.get_case("CASE-1").status, CaseStatus.RE_REVIEW)
        self.service.record_evidence(
            "CASE-1", "EV-NEW", "T2", EvidenceKind.CLASSROOM_OBSERVATION,
            "补强后试讲明显改善", date(2026, 4, 26), "复评观察组",
        )
        self.service.open_scoring("CASE-1", date(2026, 4, 27))
        good = entries(L1=4, L2=4, T1=4, T2=4, C1=4)
        self.service.submit_score_sheet("CASE-1", "SH2-A1", "A1", good, date(2026, 4, 28))
        self.service.submit_score_sheet("CASE-1", "SH2-A2", "A2", good, date(2026, 4, 28))

    def test_re_review_must_reference_original_decision(self):
        with self.assertRaises(DecisionError) as ctx:
            self.service.make_decision(
                "CASE-1", "D2", DecisionOutcome.PASS, "缺少引用", date(2026, 5, 2)
            )
        self.assertIn("原决定", str(ctx.exception))

    def test_re_review_must_include_new_evidence(self):
        with self.assertRaises(DecisionError) as ctx:
            self.service.make_decision(
                "CASE-1", "D2", DecisionOutcome.PASS, "缺少新证据",
                date(2026, 5, 2), supersedes_decision_id="D1",
            )
        self.assertIn("新增证据", str(ctx.exception))

    def test_re_review_evidence_must_postdate_original_decision(self):
        with self.assertRaises(DecisionError):
            self.service.make_decision(
                "CASE-1", "D2", DecisionOutcome.PASS, "旧证据冒充",
                date(2026, 5, 2), supersedes_decision_id="D1",
                new_evidence_ids=("EV-OBS",),  # 2026-02-10 登记，早于 D1
            )

    def test_re_review_pass_issues_certificate(self):
        self.service.make_decision(
            "CASE-1", "D2", DecisionOutcome.PASS, "复评通过",
            date(2026, 5, 2), supersedes_decision_id="D1", new_evidence_ids=("EV-NEW",),
        )
        case = self.service.get_case("CASE-1")
        self.assertEqual(case.status, CaseStatus.CLOSED)
        cert = self.service.valid_certificate_for("CAND-1")
        self.assertIsNotNone(cert)
        self.assertEqual(cert.decision_id, "D2")


class CertificateUniquenessTests(unittest.TestCase):
    def test_second_pass_decision_blocked_after_close(self):
        service = drive_to_confirmed(make_service())
        service.make_decision("CASE-1", "D1", DecisionOutcome.PASS, "通过", date(2026, 3, 1))
        with self.assertRaises(InvalidStateError):
            service.make_decision("CASE-1", "D2", DecisionOutcome.PASS, "重复", date(2026, 3, 2))

    def test_second_case_cannot_yield_second_valid_certificate(self):
        service = drive_to_confirmed(make_service())
        service.make_decision("CASE-1", "D1", DecisionOutcome.PASS, "通过", date(2026, 3, 1))
        # 首案已结案，可再立案；但发证时撞上有效证书
        drive_to_confirmed(service, case_id="CASE-2", candidate_id="CAND-1", prefix="C2-")
        with self.assertRaises(DuplicateCertificateError):
            service.make_decision("CASE-2", "D2", DecisionOutcome.PASS, "再评", date(2026, 4, 1))
        # 注销首张后方可再签发
        service.revoke_certificate("CERT-CASE-1-D1", date(2026, 4, 15))
        service.make_decision("CASE-2", "D2", DecisionOutcome.PASS, "再评", date(2026, 4, 20))
        valid = [
            c for c in service.store.certificates.values()
            if c.candidate_id == "CAND-1" and c.status is CertificateStatus.VALID
        ]
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0].decision_id, "D2")

    def test_concurrent_pass_decisions_produce_single_certificate(self):
        service = drive_to_confirmed(make_service())
        barrier = threading.Barrier(2)
        results, errors = [], []

        def decide(decision_id):
            try:
                barrier.wait(timeout=5)
                results.append(
                    service.make_decision(
                        "CASE-1", decision_id, DecisionOutcome.PASS, "通过", date(2026, 3, 1)
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 测试需要收集任意领域异常
                errors.append(exc)

        threads = [threading.Thread(target=decide, args=(f"D-{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        valid = [
            c for c in service.store.certificates.values()
            if c.candidate_id == "CAND-1" and c.status is CertificateStatus.VALID
        ]
        self.assertEqual(len(valid), 1)


if __name__ == "__main__":
    unittest.main()
