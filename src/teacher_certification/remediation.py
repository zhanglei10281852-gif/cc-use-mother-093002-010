"""补强计划：步骤依赖、完成期限与导师确认。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .errors import StateError, ValidationError


@dataclass
class RemediationStep:
    """补强步骤：可声明前置依赖，完成需导师确认。"""

    step_id: str
    plan_id: str
    title: str
    indicator_codes: tuple[str, ...]
    depends_on: tuple[str, ...]
    deadline: date
    completed_on: date | None = None
    mentor_id: str | None = None

    def is_completed_as_of(self, on: date) -> bool:
        return self.completed_on is not None and self.completed_on <= on

    def is_overdue(self, on: date) -> bool:
        return self.completed_on is None and self.deadline < on


@dataclass
class RemediationPlan:
    """挂在附条件通过或补强后复评决定下的补强计划。"""

    plan_id: str
    case_id: str
    decision_id: str
    created_on: date
    steps: list[RemediationStep] = field(default_factory=list)

    def step(self, step_id: str) -> RemediationStep:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        from .errors import NotFoundError

        raise NotFoundError(f"补强步骤不存在: {step_id}")

    def is_complete(self, on: date | None = None) -> bool:
        if not self.steps:
            return False
        if on is None:
            return all(s.completed_on is not None for s in self.steps)
        return all(s.is_completed_as_of(on) for s in self.steps)

    def ready_steps(self) -> list[RemediationStep]:
        """前置依赖已全部完成、可以开始的步骤。"""
        done = {s.step_id for s in self.steps if s.completed_on is not None}
        return [
            s
            for s in self.steps
            if s.completed_on is None and all(d in done for d in s.depends_on)
        ]

    def assert_dependencies_acyclic(self) -> None:
        """拓扑排序校验依赖图无环。"""
        known = {s.step_id for s in self.steps}
        for item in self.steps:
            unknown = set(item.depends_on) - known
            if unknown:
                raise ValidationError(f"步骤 {item.step_id} 依赖了不存在的步骤: {sorted(unknown)}")
        permanent: set[str] = set()
        temporary: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in permanent:
                return
            if step_id in temporary:
                raise ValidationError("补强步骤依赖存在循环")
            temporary.add(step_id)
            for dep in self.step(step_id).depends_on:
                visit(dep)
            temporary.discard(step_id)
            permanent.add(step_id)

        for item in self.steps:
            visit(item.step_id)

    def check_can_complete(self, step: RemediationStep, on: date) -> None:
        if step.completed_on is not None:
            raise StateError(f"步骤 {step.step_id} 已完成")
        blocking = [d for d in step.depends_on if not self.step(d).is_completed_as_of(on)]
        if blocking:
            raise StateError(f"前置步骤尚未完成: {blocking}")
