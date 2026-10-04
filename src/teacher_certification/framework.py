"""带版本的能力框架：维度、指标、决策政策与有效期。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import Enum

from .errors import NotFoundError, ValidationError


class Dimension(Enum):
    """三维能力。"""

    LANGUAGE = "语言能力"
    TEACHING = "教学技能"
    INTERCULTURAL = "跨文化素养"


@dataclass(frozen=True)
class Indicator:
    """框架内的一条可观察指标。"""

    code: str
    dimension: Dimension
    name: str
    weight: float = 1.0
    pass_mark: float = 60.0

    def __post_init__(self) -> None:
        if not self.code or not self.name:
            raise ValidationError("指标编码与名称不能为空")
        if self.weight <= 0:
            raise ValidationError("指标权重必须为正数")
        if not 0 <= self.pass_mark <= 100:
            raise ValidationError("指标合格线必须在 0 到 100 之间")


@dataclass(frozen=True)
class DecisionPolicy:
    """评分偏离阈值与结论分档政策。"""

    score_spread_threshold: float = 15.0
    conditional_margin: float = 5.0
    remediation_floor: float = 20.0

    def __post_init__(self) -> None:
        if self.score_spread_threshold <= 0:
            raise ValidationError("评分偏离阈值必须为正数")
        if not 0 < self.conditional_margin <= self.remediation_floor:
            raise ValidationError("附条件余量必须为正且不超过补强下限")


@dataclass(frozen=True)
class FrameworkVersion:
    """框架的一次发布版本，带有生效区间。"""

    framework_id: str
    revision: int
    title: str
    indicators: tuple[Indicator, ...]
    effective_from: date
    effective_to: date | None
    policy: DecisionPolicy = DecisionPolicy()
    published: bool = True

    def __post_init__(self) -> None:
        if not self.framework_id or not self.title:
            raise ValidationError("框架版本信息不完整")
        if self.revision < 1:
            raise ValidationError("版本号必须从 1 开始")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValidationError("生效区间不合法")
        if not self.indicators:
            raise ValidationError("框架版本至少包含一条指标")
        codes = [i.code for i in self.indicators]
        if len(set(codes)) != len(codes):
            raise ValidationError("指标编码在同一版本内必须唯一")
        covered = {i.dimension for i in self.indicators}
        if covered != set(Dimension):
            raise ValidationError("框架版本必须覆盖语言、教学、跨文化三个维度")

    def is_expired(self, on: date) -> bool:
        return self.effective_to is not None and on > self.effective_to

    def is_active(self, on: date) -> bool:
        return self.published and self.effective_from <= on and not self.is_expired(on)

    def indicator(self, code: str) -> Indicator:
        for item in self.indicators:
            if item.code == code:
                return item
        raise NotFoundError(f"指标不存在: {code}")

    def dimension_requirement(self, dimension: Dimension) -> float:
        """某维度的合格要求：该维度指标合格线的加权平均。"""
        items = [i for i in self.indicators if i.dimension is dimension]
        total_weight = sum(i.weight for i in items)
        return sum(i.pass_mark * i.weight for i in items) / total_weight


@dataclass
class Framework:
    """框架聚合：持有按版本号递增的发布历史。"""

    framework_id: str
    name: str
    versions: list[FrameworkVersion] = field(default_factory=list)

    def version(self, revision: int) -> FrameworkVersion:
        for item in self.versions:
            if item.revision == revision:
                return item
        raise NotFoundError(f"框架 {self.framework_id} 不存在版本 {revision}")

    def active_version(self, on: date) -> FrameworkVersion:
        candidates = [v for v in self.versions if v.is_active(on)]
        if not candidates:
            raise NotFoundError(f"框架 {self.framework_id} 在 {on} 没有有效版本")
        return max(candidates, key=lambda v: v.revision)

    def add_version(self, version: FrameworkVersion) -> None:
        if version.framework_id != self.framework_id:
            raise ValidationError("版本归属的框架不一致")
        expected = len(self.versions) + 1
        if version.revision != expected:
            raise ValidationError(f"版本号必须连续，下一个应为 {expected}")
        if version.published and self.versions:
            previous = self.versions[-1]
            if version.effective_from <= previous.effective_from:
                raise ValidationError("新版本的生效日期必须晚于上一版本")
            if previous.published and previous.effective_to is None:
                # 新版本发布时自动关闭上一版本的开放区间。
                closed = replace(previous, effective_to=version.effective_from - timedelta(days=1))
                self.versions[-1] = closed
        self.versions.append(version)
