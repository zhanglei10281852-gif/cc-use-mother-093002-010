"""认证服务的持久化仓库。

Store 汇总全部实体集合；JsonFileRepository 以原子写方式落盘，
服务重启后从同一文件恢复，待校准案件等中间状态保持不变。
"""
from __future__ import annotations

import dataclasses
import json
import os
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .codec import decode, encode
from .model import (
    Assessor,
    CalibrationSession,
    Candidate,
    Certificate,
    CertificationCase,
    ConflictDeclaration,
    Decision,
    EvidenceItem,
    FrameworkVersion,
    ObservationTask,
    RemediationPlan,
    ScoreSheet,
)


@dataclass
class Store:
    frameworks: dict[str, FrameworkVersion] = field(default_factory=dict)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    assessors: dict[str, Assessor] = field(default_factory=dict)
    cases: dict[str, CertificationCase] = field(default_factory=dict)
    tasks: dict[str, ObservationTask] = field(default_factory=dict)
    evidence: dict[str, EvidenceItem] = field(default_factory=dict)
    sheets: dict[str, ScoreSheet] = field(default_factory=dict)
    declarations: dict[str, ConflictDeclaration] = field(default_factory=dict)
    calibrations: dict[str, CalibrationSession] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    plans: dict[str, RemediationPlan] = field(default_factory=dict)
    certificates: dict[str, Certificate] = field(default_factory=dict)


class InMemoryRepository:
    """不落盘的仓库，用于测试与一次性进程。"""

    def __init__(self, store: Optional[Store] = None):
        self._store = store or Store()

    def load(self) -> Store:
        return self._store

    def save(self, store: Store) -> None:
        self._store = store


class JsonFileRepository:
    """把 Store 整体快照写入 JSON 文件（临时文件 + 原子替换）。"""

    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> Store:
        if not self.path.exists():
            return Store()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        hints = typing.get_type_hints(Store)
        store = Store()
        for f in dataclasses.fields(Store):
            element_type = hints[f.name].__args__[1]
            setattr(
                store,
                f.name,
                {key: decode(element_type, value) for key, value in raw[f.name].items()},
            )
        return store

    def save(self, store: Store) -> None:
        raw = {
            f.name: {key: encode(value) for key, value in getattr(store, f.name).items()}
            for f in dataclasses.fields(Store)
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
