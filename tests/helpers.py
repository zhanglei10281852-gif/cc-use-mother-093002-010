import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from teacher_certification.case import CaseStatus
from teacher_certification.framework import Dimension, Indicator
from teacher_certification.service import CertificationService

ALL_DIMS = {Dimension.LANGUAGE, Dimension.TEACHING, Dimension.INTERCULTURAL}
CODES = ("LANG-1", "LANG-2", "TEACH-1", "TEACH-2", "CULT-1")


def make_indicators():
    return [
        Indicator("LANG-1", Dimension.LANGUAGE, "语言准确性", pass_mark=70),
        Indicator("LANG-2", Dimension.LANGUAGE, "语言流利度", pass_mark=70),
        Indicator("TEACH-1", Dimension.TEACHING, "课堂组织", pass_mark=70),
        Indicator("TEACH-2", Dimension.TEACHING, "互动设计", pass_mark=70),
        Indicator("CULT-1", Dimension.INTERCULTURAL, "文化调适", pass_mark=70),
    ]


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_path = Path(self.tmp.name) / "store.json"
        self.service = CertificationService(self.store_path)
        self.today = date(2026, 3, 1)
        self.framework_id = self.service.create_framework("国际中文教师三维能力框架")
        self.service.add_framework_version(
            self.framework_id, "2026版", make_indicators(), date(2026, 1, 1)
        )
        self.a1 = self.service.register_assessor("评委甲", ALL_DIMS)
        self.a2 = self.service.register_assessor("评委乙", ALL_DIMS)
        self.a3 = self.service.register_assessor("评委丙", ALL_DIMS)
        self.mentor = self.service.register_assessor("导师丁", ALL_DIMS, is_mentor=True)
        self.candidate = self.service.register_candidate("候选人")

    def reopen_service(self):
        """模拟服务重启：同一存储路径重新实例化。"""
        self.service = CertificationService(self.store_path)

    def open_case(self, candidate=None):
        return self.service.open_case(
            candidate or self.candidate,
            self.framework_id,
            [self.a1, self.a2, self.a3],
            self.today,
        )

    @staticmethod
    def scores(value, **overrides):
        result = {code: float(value) for code in CODES}
        result.update({key: float(val) for key, val in overrides.items()})
        return result

    def declare_all(self, case_id):
        for assessor in (self.a1, self.a2, self.a3):
            self.service.declare_conflict(case_id, assessor, False, "", self.today)

    def drive_scoring(self, case_id, s1, s2, s3):
        if self.service.get_case(case_id).status is CaseStatus.OPEN:
            self.service.begin_scoring(case_id)
        self.declare_all(case_id)
        self.service.submit_scores(case_id, self.a1, s1, self.today)
        self.service.submit_scores(case_id, self.a2, s2, self.today)
        self.service.submit_scores(case_id, self.a3, s3, self.today)
