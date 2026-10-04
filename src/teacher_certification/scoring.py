"""独立评分、利益冲突申报、偏离识别与成绩汇总。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .errors import ValidationError
from .framework import Dimension, FrameworkVersion


class SheetStatus(Enum):
    SUBMITTED = "已提交"
    EXCLUDED = "已排除"


@dataclass(frozen=True)
class ScoreEntry:
    indicator_code: str
    value: float

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 100:
            raise ValidationError("评分必须在 0 到 100 之间")


@dataclass
class ScoreSheet:
    """一位评委在一轮评分中提交的完整评分表，提交后不可更改。"""

    sheet_id: str
    case_id: str
    assessor_id: str
    round_no: int
    entries: tuple[ScoreEntry, ...]
    submitted_on: date
    status: SheetStatus = SheetStatus.SUBMITTED

    def value_for(self, indicator_code: str) -> float | None:
        for entry in self.entries:
            if entry.indicator_code == indicator_code:
                return entry.value
        return None


@dataclass
class ConflictDeclaration:
    """评委在评分前对某案件的利益冲突申报。"""

    case_id: str
    assessor_id: str
    has_conflict: bool
    detail: str
    declared_on: date


@dataclass(frozen=True)
class Deviation:
    """某一指标上评委评分超出阈值的偏离。"""

    indicator_code: str
    spread: float
    values: tuple[float, ...]


def detect_deviations(version: FrameworkVersion, sheets: list[ScoreSheet]) -> list[Deviation]:
    """对每条规定指标检查有效评分表之间的极差。"""
    threshold = version.policy.score_spread_threshold
    deviations: list[Deviation] = []
    for indicator in version.indicators:
        values = [s.value_for(indicator.code) for s in sheets]
        present = tuple(v for v in values if v is not None)
        if len(present) < 2:
            continue
        spread = max(present) - min(present)
        if spread > threshold:
            deviations.append(Deviation(indicator.code, spread, present))
    return deviations


def effective_values(
    sheets: list[ScoreSheet], revisions: tuple
) -> dict[str, dict[str, float]]:
    """评分表原始值叠加校准修订后的每位评委有效值。"""
    values: dict[str, dict[str, float]] = {}
    for sheet in sheets:
        values[sheet.assessor_id] = {e.indicator_code: e.value for e in sheet.entries}
    for revision in revisions:
        sheet_values = values.get(revision.assessor_id)
        if sheet_values is not None:
            sheet_values[revision.indicator_code] = revision.new_value
    return values


def aggregate_dimension_scores(
    version: FrameworkVersion,
    sheets: list[ScoreSheet],
    revisions: tuple = (),
    overrides: dict[str, float] | None = None,
) -> dict[Dimension, float]:
    """按指标均值、维度加权汇总三维得分；复核裁定值优先。"""
    overrides = overrides or {}
    values = effective_values(sheets, revisions)
    indicator_scores: dict[str, float] = {}
    for indicator in version.indicators:
        if indicator.code in overrides:
            indicator_scores[indicator.code] = overrides[indicator.code]
            continue
        per_assessor = [v[indicator.code] for v in values.values() if indicator.code in v]
        if not per_assessor:
            raise ValidationError(f"指标 {indicator.code} 没有有效评分")
        indicator_scores[indicator.code] = sum(per_assessor) / len(per_assessor)
    result: dict[Dimension, float] = {}
    for dimension in Dimension:
        items = [i for i in version.indicators if i.dimension is dimension]
        total_weight = sum(i.weight for i in items)
        result[dimension] = (
            sum(indicator_scores[i.code] * i.weight for i in items) / total_weight
        )
    return result
