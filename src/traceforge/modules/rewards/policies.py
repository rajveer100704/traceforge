"""Versioned reward policies.

The default weights are a documented engineering choice, NOT empirically optimised values.
Correctness, regression, safety and evidence integrity are hard gates; efficiency is a bounded
secondary signal that can never outweigh task completion (enforced by RewardPolicy validation).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from traceforge.contracts.common import NotFoundError
from traceforge.contracts.rewards import ComponentKind, ComponentSpec, GateKind, RewardPolicy

VERIFIED_V1 = RewardPolicy(
    policy_id="verified-v1",
    version="1.0.0",
    description="R3: hidden execution + regression + safety + evidence integrity gates.",
    components=(
        ComponentSpec(kind=ComponentKind.TASK_COMPLETION, weight=0.7),
        ComponentSpec(kind=ComponentKind.REGRESSION, weight=0.1),
        ComponentSpec(kind=ComponentKind.EVIDENCE, weight=0.1),
        ComponentSpec(kind=ComponentKind.EFFICIENCY, weight=0.1),
    ),
    hard_gates=(
        GateKind.AUTHORITATIVE,
        GateKind.VERDICT_PASS,
        GateKind.SAFETY,
        GateKind.REGRESSION,
        GateKind.EVIDENCE_INTEGRITY,
    ),
)

SELF_REPORT_R1 = RewardPolicy(
    policy_id="self-report-r1",
    version="1.0.0",
    description="R1 research-only baseline: rewards the agent's own success claim.",
    components=(ComponentSpec(kind=ComponentKind.SELF_REPORT, weight=1.0),),
    hard_gates=(),
    research_only=True,
)

VISIBLE_TESTS_R2 = RewardPolicy(
    policy_id="visible-tests-r2",
    version="1.0.0",
    description="R2 research-only baseline: rewards passing visible tests as found in workspace.",
    components=(ComponentSpec(kind=ComponentKind.VISIBLE_TESTS, weight=1.0),),
    hard_gates=(),
    research_only=True,
)

BUILTIN_POLICIES: dict[str, RewardPolicy] = {
    p.policy_id: p for p in (VERIFIED_V1, SELF_REPORT_R1, VISIBLE_TESTS_R2)
}

# The verifier profile each policy is designed to consume.
POLICY_PROFILE: dict[str, str] = {
    "verified-v1": "v3_full",
    "self-report-r1": "v1_self_report",
    "visible-tests-r2": "v2_visible_tests",
}


class PolicyRegistry:
    def __init__(self, policies: dict[str, RewardPolicy] | None = None) -> None:
        self._by_key: dict[tuple[str, str], RewardPolicy] = {}
        self._latest: dict[str, str] = {}
        for p in (policies or BUILTIN_POLICIES).values():
            self.add(p)

    def add(self, policy: RewardPolicy) -> None:
        key = (policy.policy_id, policy.version)
        existing = self._by_key.get(key)
        if existing is not None and existing.policy_hash != policy.policy_hash:
            raise ValueError(f"policy {key} is immutable; bump the version to change it")
        self._by_key[key] = policy
        self._latest[policy.policy_id] = max(
            self._latest.get(policy.policy_id, "0.0.0"), policy.version, key=_semver
        )

    def get(self, policy_id: str, version: str | None = None) -> RewardPolicy:
        version = version or self._latest.get(policy_id)
        policy = self._by_key.get((policy_id, version or ""))
        if policy is None:
            raise NotFoundError(f"unknown reward policy {policy_id}@{version}")
        return policy

    def all(self) -> list[RewardPolicy]:
        return sorted(self._by_key.values(), key=lambda p: (p.policy_id, _semver(p.version)))

    def load_yaml(self, path: Path) -> None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in data.get("policies", []):
            self.add(RewardPolicy.model_validate(raw))


def _semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit())
