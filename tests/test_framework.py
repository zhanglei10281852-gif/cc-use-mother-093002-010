from datetime import date

from helpers import ServiceTestCase, make_indicators
from teacher_certification.errors import NotFoundError, StateError, ValidationError
from teacher_certification.framework import Dimension, FrameworkVersion, Indicator


class FrameworkTests(ServiceTestCase):
    def test_new_version_closes_previous_open_range(self):
        self.service.add_framework_version(
            self.framework_id, "2026秋版", make_indicators(), date(2026, 9, 1)
        )
        framework = self.service.get_framework(self.framework_id)
        self.assertEqual(framework.version(1).effective_to, date(2026, 8, 31))
        self.assertEqual(framework.active_version(date(2026, 7, 1)).revision, 1)
        self.assertEqual(framework.active_version(date(2026, 9, 15)).revision, 2)

    def test_revision_must_be_sequential(self):
        framework = self.service.get_framework(self.framework_id)
        with self.assertRaises(ValidationError):
            framework.add_version(
                FrameworkVersion(
                    framework_id=self.framework_id,
                    revision=5,
                    title="跳号版本",
                    indicators=tuple(make_indicators()),
                    effective_from=date(2026, 9, 1),
                    effective_to=None,
                )
            )

    def test_duplicate_indicator_code_rejected(self):
        indicators = make_indicators()
        indicators.append(Indicator("LANG-1", Dimension.LANGUAGE, "重复编码"))
        with self.assertRaises(ValidationError):
            self.service.add_framework_version(
                self.framework_id, "非法版本", indicators, date(2026, 9, 1)
            )

    def test_version_must_cover_three_dimensions(self):
        indicators = [i for i in make_indicators() if i.dimension is not Dimension.INTERCULTURAL]
        with self.assertRaises(ValidationError):
            self.service.add_framework_version(
                self.framework_id, "缺维度版本", indicators, date(2026, 9, 1)
            )

    def test_open_case_requires_active_version(self):
        expired_id = self.service.create_framework("已过期框架")
        self.service.add_framework_version(
            expired_id, "旧版", make_indicators(), date(2025, 1, 1), date(2025, 12, 31)
        )
        with self.assertRaises(NotFoundError):
            self.service.open_case(
                self.candidate, expired_id, [self.a1, self.a2, self.a3], self.today
            )

    def test_case_requires_two_qualified_assessors_per_dimension(self):
        lang_only_1 = self.service.register_assessor("语言评委一", {Dimension.LANGUAGE})
        lang_only_2 = self.service.register_assessor("语言评委二", {Dimension.LANGUAGE})
        with self.assertRaises(ValidationError):
            self.service.open_case(
                self.candidate, self.framework_id, [lang_only_1, lang_only_2], self.today
            )

    def test_candidate_cannot_have_two_open_cases(self):
        self.open_case()
        with self.assertRaises(StateError):
            self.open_case()


if __name__ == "__main__":
    import unittest

    unittest.main()
