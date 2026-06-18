from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class SourceDecision:
    domain: str
    selected_source: str
    fallback_sources: tuple[str, ...] = ()
    status: str = "unknown"
    reason: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TechnicalAnalysisDataPlan:
    symbol: str
    decisions: tuple[SourceDecision, ...]
    warnings: tuple[str, ...] = ()

    def decisions_for_domain(self, domain: str) -> tuple[SourceDecision, ...]:
        clean = str(domain or "").strip().lower()
        return tuple(decision for decision in self.decisions if decision.domain.lower() == clean)
