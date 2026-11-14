"""Execution budgets (actions, wall clock, tokens, cost)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from traceforge.contracts.tasks import ResourceBudget


@dataclass
class BudgetManager:
    budget: ResourceBudget
    actions: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    # False once any generation's cost could not be determined; the running total is then a
    # lower bound, so a configured cost budget can no longer be enforced and fails closed.
    cost_known: bool = True
    _started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def charge_action(self) -> None:
        self.actions += 1

    def charge_generation(self, tokens: int, cost: float | None) -> None:
        self.tokens += tokens
        if cost is None:
            self.cost_known = False
        else:
            self.cost_usd += cost

    def exhausted(self) -> str | None:
        """Return the exhausted dimension, or None while budget remains."""
        if self.actions >= self.budget.max_actions:
            return "max_actions"
        if self.elapsed() >= self.budget.max_wall_seconds:
            return "wall_clock"
        if self.tokens >= self.budget.max_tokens:
            return "max_tokens"
        if self.budget.max_cost_usd > 0:
            if not self.cost_known:
                return "max_cost_unverifiable"
            if self.cost_usd >= self.budget.max_cost_usd:
                return "max_cost"
        return None
