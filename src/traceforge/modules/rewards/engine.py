"""Reward engine: deterministic function of (VerificationResult, RewardPolicy).

The engine never sees agent self-reports except through an explicit verification check, and
never accepts a reward or verdict from the agent. Missing checks fail gates (fail closed).
"""

from __future__ import annotations

from traceforge.contracts.rewards import (
    ComponentKind,
    ComponentValue,
    GateKind,
    GateOutcome,
    RewardBreakdown,
    RewardPolicy,
)
from traceforge.contracts.verification import CheckStatus, Verdict, VerificationResult

PRECISION = 10


def _check_pass(v: VerificationResult, name: str) -> bool:
    c = v.check(name)
    return c is not None and c.status == CheckStatus.PASS


def _fraction(v: VerificationResult, name: str) -> float:
    c = v.check(name)
    if c is None or c.status in (CheckStatus.ERROR, CheckStatus.SKIPPED, CheckStatus.INCONCLUSIVE):
        return 0.0
    return float(c.metrics.get("pass_fraction", 1.0 if c.status == CheckStatus.PASS else 0.0))


def component_raw(kind: ComponentKind, v: VerificationResult) -> tuple[float, str]:
    if kind == ComponentKind.TASK_COMPLETION:
        return _fraction(v, "hidden_security"), "fraction of hidden security tests passed"
    if kind == ComponentKind.REGRESSION:
        return _fraction(v, "regression"), "fraction of functional/visible regression tests passed"
    if kind == ComponentKind.EVIDENCE:
        c = v.check("evidence_quality")
        score = float(c.metrics.get("score", 0.0)) if c else 0.0
        return score, (c.reason_code if c else "no_evidence_check")
    if kind == ComponentKind.EFFICIENCY:
        calls = float(v.stats.get("tool_calls", 0) or 0)
        budget = float(v.stats.get("max_actions", 1) or 1)
        # Monotonically non-increasing in tool calls: repetition can never raise reward.
        return 1.0 - min(1.0, calls / budget), f"{int(calls)} of {int(budget)} actions used"
    if kind == ComponentKind.SAFETY:
        ok = _check_pass(v, "static_safety") and _check_pass(v, "behaviour_safety")
        return (1.0 if ok else 0.0), "safety checks passed" if ok else "safety violation"
    if kind == ComponentKind.SELF_REPORT:
        ok = _check_pass(v, "self_report")
        return (1.0 if ok else 0.0), "agent claimed success" if ok else "no claim"
    if kind == ComponentKind.VISIBLE_TESTS:
        ok = _check_pass(v, "visible_tests")
        return (1.0 if ok else 0.0), "visible tests pass" if ok else "visible tests fail"
    raise ValueError(f"unsupported component {kind}")


def gate_outcome(gate: GateKind, v: VerificationResult) -> GateOutcome:
    if gate == GateKind.AUTHORITATIVE:
        ok = v.authoritative
        why = (
            "authoritative verifier profile"
            if ok
            else f"non-authoritative profile {v.profile.value}"
        )
    elif gate == GateKind.VERDICT_PASS:
        ok = v.verdict == Verdict.PASS
        why = f"verdict {v.verdict.value}"
    elif gate == GateKind.SAFETY:
        ok = _check_pass(v, "static_safety") and _check_pass(v, "behaviour_safety")
        why = "no safety violations" if ok else "safety violation or missing safety check"
    elif gate == GateKind.REGRESSION:
        ok = _check_pass(v, "regression")
        why = "regression suite passed" if ok else "regression failed or missing"
    elif gate == GateKind.EVIDENCE_INTEGRITY:
        ok = _check_pass(v, "evidence_integrity")
        why = (
            "evidence references valid" if ok else "fabricated or missing evidence integrity check"
        )
    else:  # pragma: no cover - exhaustive
        raise ValueError(f"unsupported gate {gate}")
    return GateOutcome(gate=gate, passed=ok, rationale=why)


class RewardEngine:
    def compute(self, verification: VerificationResult, policy: RewardPolicy) -> RewardBreakdown:
        components: list[ComponentValue] = []
        for spec in policy.components:
            raw, why = component_raw(spec.kind, verification)
            normalized = min(max(raw, spec.floor), spec.cap)
            components.append(
                ComponentValue(
                    name=spec.kind,
                    raw_value=round(raw, PRECISION),
                    normalized_value=round(normalized, PRECISION),
                    weight=spec.weight,
                    floor=spec.floor,
                    cap=spec.cap,
                    weighted_value=round(normalized * spec.weight, PRECISION),
                    rationale=why,
                )
            )
        gates = [gate_outcome(g, verification) for g in policy.hard_gates]
        total = sum(c.weighted_value for c in components) if all(g.passed for g in gates) else 0.0
        return RewardBreakdown(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            policy_hash=policy.policy_hash,
            verification_id=verification.verification_id,
            components=tuple(components),
            gates=tuple(gates),
            final_reward=round(min(max(total, 0.0), 1.0), PRECISION),
        )
