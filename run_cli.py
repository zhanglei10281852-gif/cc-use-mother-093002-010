"""命令行冒烟：完整走一遍三维认证流程并打印秘书视角快照。"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "tests"))

from support import drive_full_journey, make_service  # noqa: E402

from teacher_certification.model import Dimension  # noqa: E402


def main() -> None:
    service = drive_full_journey(make_service())
    snap = service.certification_snapshot("CAND-1", date(2026, 5, 3))
    report = {
        "候选人": snap.candidate_id,
        "查询日期": str(snap.as_of),
        "案件状态": snap.case_status.value,
        "当时有效决定": {
            "结论": snap.effective_decision.outcome.value,
            "理由": snap.effective_decision.rationale,
            "引用原决定": snap.effective_decision.supersedes_decision_id,
            "新增证据": list(snap.effective_decision.new_evidence_ids),
        },
        "三维差距": {
            g.dimension.value: {
                "均分": round(g.average, 2),
                "合格线": g.threshold,
                "差距": round(g.gap, 2),
                "达标": g.meets_threshold,
            }
            for g in snap.dimension_gaps
        },
        "校准过程": [
            {"会话": s.session_id, "状态": s.status.value, "争议指标": [f.indicator_id for f in s.trigger]}
            for s in snap.calibration_trail
        ],
        "未完成补强步骤": [s.step_id for s in snap.outstanding_steps],
        "证书": snap.certificate.certificate_id if snap.certificate else None,
    }
    assert set(report["三维差距"]) == {d.value for d in Dimension}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
