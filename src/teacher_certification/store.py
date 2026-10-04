"""基于 JSON 文件的持久化仓储，进程内用锁串行化写入。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .calibration import CalibrationSession, ReviewRequest
from .case import CertificationCase
from .certificate import Certificate
from .decision import CertificationDecision
from .evidence import Evidence, ObservationTask
from .framework import Framework
from .people import Assessor, Candidate
from .remediation import RemediationPlan
from .scoring import ConflictDeclaration, ScoreSheet
from .serde import from_jsonable, to_jsonable

COLLECTIONS: dict[str, type] = {
    "frameworks": Framework,
    "candidates": Candidate,
    "assessors": Assessor,
    "cases": CertificationCase,
    "tasks": ObservationTask,
    "evidence": Evidence,
    "declarations": ConflictDeclaration,
    "sheets": ScoreSheet,
    "calibrations": CalibrationSession,
    "reviews": ReviewRequest,
    "decisions": CertificationDecision,
    "plans": RemediationPlan,
    "certificates": Certificate,
}


class JsonStore:
    """把所有聚合保存在一个 JSON 文件中，保存时原子替换。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data: dict[str, dict[str, object]] = {name: {} for name in COLLECTIONS}
        self.seq: dict[str, int] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.seq = dict(raw.get("seq", {}))
            for name, cls in COLLECTIONS.items():
                self.data[name] = {
                    key: from_jsonable(value, cls)
                    for key, value in raw.get("collections", {}).get(name, {}).items()
                }

    def save(self) -> None:
        payload = {
            "seq": self.seq,
            "collections": {
                name: {key: to_jsonable(value) for key, value in self.data[name].items()}
                for name in COLLECTIONS
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, self.path)

    def next_id(self, prefix: str) -> str:
        number = self.seq.get(prefix, 0) + 1
        self.seq[prefix] = number
        return f"{prefix}-{number:04d}"

    def collection(self, name: str) -> dict[str, object]:
        return self.data[name]
