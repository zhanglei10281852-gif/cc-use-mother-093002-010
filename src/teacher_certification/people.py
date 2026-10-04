"""候选人、评委与导师。"""
from __future__ import annotations

from dataclasses import dataclass

from .framework import Dimension


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    name: str


@dataclass(frozen=True)
class Assessor:
    """评委：按维度持有评分资质，可兼任补强导师。"""

    assessor_id: str
    name: str
    qualified_dimensions: frozenset[Dimension]
    is_mentor: bool = False
    active: bool = True

    def qualified_for(self, dimension: Dimension) -> bool:
        return self.active and dimension in self.qualified_dimensions
