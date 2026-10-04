"""命令行冒烟：端到端走一遍三维认证流程并打印关键节点。"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from teacher_certification import (
    CertificationService,
    Dimension,
    EvidenceKind,
    Indicator,
    secretary_briefing,
)

ALL_DIMS = {Dimension.LANGUAGE, Dimension.TEACHING, Dimension.INTERCULTURAL}


def main() -> None:
    store_path = Path(tempfile.mkdtemp(prefix="cert-demo-")) / "store.json"
    service = CertificationService(store_path)
    today = date(2026, 3, 1)

    framework_id = service.create_framework("国际中文教师三维能力框架")
    service.add_framework_version(
        framework_id,
        "2026版",
        [
            Indicator("LANG-1", Dimension.LANGUAGE, "语言准确性", pass_mark=70),
            Indicator("LANG-2", Dimension.LANGUAGE, "语言流利度", pass_mark=70),
            Indicator("TEACH-1", Dimension.TEACHING, "课堂组织", pass_mark=70),
            Indicator("TEACH-2", Dimension.TEACHING, "互动设计", pass_mark=70),
            Indicator("CULT-1", Dimension.INTERCULTURAL, "文化调适", pass_mark=70),
        ],
        date(2026, 1, 1),
    )
    assessors = [service.register_assessor(name, ALL_DIMS) for name in ("评委甲", "评委乙", "评委丙")]
    mentor = service.register_assessor("导师丁", ALL_DIMS, is_mentor=True)
    candidate = service.register_candidate("候选人王老师")

    case_id = service.open_case(candidate, framework_id, assessors, today)
    task_id = service.plan_observation_task(case_id, assessors[0], ["TEACH-1", "TEACH-2"], date(2026, 3, 2))
    service.complete_observation_task(task_id, "两次课堂观察记录", date(2026, 3, 3))
    service.submit_evidence(case_id, EvidenceKind.ACADEMIC, ["LANG-1"], "教务处", "语音研究论文", date(2026, 3, 3))

    service.begin_scoring(case_id)
    for assessor in assessors:
        service.declare_conflict(case_id, assessor, False, "", today)
    base = {"LANG-1": 80.0, "LANG-2": 80.0, "TEACH-1": 80.0, "TEACH-2": 80.0, "CULT-1": 66.0}
    service.submit_scores(case_id, assessors[0], {**base, "LANG-1": 90.0}, today)
    service.submit_scores(case_id, assessors[1], {**base, "LANG-1": 90.0}, today)
    service.submit_scores(case_id, assessors[2], {**base, "LANG-1": 60.0, "CULT-1": 68.0}, today)

    session_id = next(iter(service.store().collection("calibrations")))
    service.submit_calibration_revision(session_id, assessors[2], {"LANG-1": 85.0}, date(2026, 3, 4))
    service.resolve_calibration(session_id, "统一语言准确性评分口径", date(2026, 3, 4))

    decision_id = service.record_decision(case_id, "跨文化素养略低于要求，附条件通过", date(2026, 3, 10))
    plan_id = service.create_remediation_plan(
        decision_id,
        [
            {"key": "workshop", "title": "跨文化交际工作坊", "indicator_codes": ["CULT-1"], "deadline": date(2026, 4, 1)},
            {"key": "demo", "title": "试讲与反思报告", "indicator_codes": ["CULT-1"], "depends_on": ["workshop"], "deadline": date(2026, 4, 15)},
        ],
        date(2026, 3, 10),
    )
    for step in service.get_plan(plan_id).steps:
        service.complete_remediation_step(step.step_id, mentor, date(2026, 3, 25))
    certificate_id = service.issue_certificate(candidate, date(2026, 3, 26))

    briefing = secretary_briefing(service, candidate, date(2026, 3, 26))
    print(
        json.dumps(
            {
                "证书": service.store().collection("certificates")[certificate_id].serial_no,
                "决定": briefing["decision"],
                "三维差距": briefing["dimension_gaps"],
                "校准过程": briefing["calibration_process"],
                "未完成补强": briefing["outstanding_steps"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
