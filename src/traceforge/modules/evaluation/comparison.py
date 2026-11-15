"""Paired benchmark comparison: matching, pairing, paired estimators, tiers (pure; no I/O).

Contract: ``comparison_contract.py`` / ``docs/comparison-contract.md``. Key properties:

* Episodes are paired on ``(task_id, task_version, episode_seed)``. Strict coverage refuses any
  difference in keys; ``intersection`` is diagnostic only and lists every exclusion.
* Each pair belongs to one **structural scenario**, resolved from authoritative sources only
  (``scenarios.py``). Scenarios are the primary resampling unit: seeds and task instances never
  become extra clusters.
* Point estimates reuse the PR 1B per-episode definitions (``metrics.py``) unchanged, so a
  Class A difference equals the difference of the two arms' PR 1B pooled estimands.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from traceforge.contracts.common import sha256_json
from traceforge.contracts.comparison import NEVER_VARIABLE, PROVIDER_SEED, ComparisonRequest, RunRef
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation import comparison_contract as cc
from traceforge.modules.evaluation.metrics import (
    REWARD_POSITIVE_THRESHOLD,
    _check_outcome,
    _check_pass,
    _finite_sum,
    _truly_successful,
    episode_telemetry,
    summarize,
)
from traceforge.modules.evaluation.scenarios import (
    SCENARIO_REGISTRY_VERSION,
    ScenarioError,
    ScenarioRef,
    ScenarioResolver,
)
from traceforge.modules.evaluation.statistics import (
    CLUSTER_METHOD,
    cluster_bootstrap,
    paired_cluster_ratio_bootstrap,
)
from traceforge.modules.tasks.oracle_identity import ORACLE_NORMALISATION_VERSION
from traceforge.modules.tasks.registry import SPLIT_POLICY, SPLIT_POLICY_VERSION

Key = tuple[str, str, int]
SIGNED_METRICS = frozenset({"mean_reward"})  # may be negative: no relative difference
FULL_PROFILE = "v3_full"


@dataclass
class RunData:
    """One loaded arm: its configuration, records and run-level status."""

    ref: RunRef
    config: dict[str, Any]
    records: list[Any]
    status: str = "completed"
    run_mode: str = "official"
    leakage_ok: bool | None = True
    name: str = ""
    config_hash: str = ""


class ComparisonRefused(Exception):
    """Matching failed; ``document`` is the refusal record (matching block, no metrics)."""

    def __init__(self, document: dict[str, Any]) -> None:
        codes = sorted({p["code"] for p in document["matching"]["problems"]})
        super().__init__(f"comparison refused: {', '.join(codes)}")
        self.document = document


@dataclass
class _Problems:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, code: str, detail: str, keys: Iterable[Key] = ()) -> None:
        severity = cc.PROBLEM_CODES[code][0]
        p = self.items.setdefault(
            code, {"code": code, "severity": severity, "detail": detail, "keys": [], "count": 0}
        )
        p["count"] += 1
        p["keys"].extend([list(k) for k in keys])
        if p["count"] > 1 and detail not in p["detail"]:
            p["detail"] = p["detail"] if len(p["detail"]) > 500 else f"{p['detail']}; {detail}"

    def severities(self) -> set[str]:
        return {p["severity"] for p in self.items.values()}

    def codes(self) -> set[str]:
        return set(self.items)

    def as_list(self) -> list[dict[str, Any]]:
        out = []
        for code in sorted(self.items):
            p = dict(self.items[code])
            p["keys"] = sorted(p["keys"])[:200]
            out.append(p)
        return out


@dataclass(frozen=True)
class Pair:
    key: Key
    base: Any
    cand: Any
    ref: ScenarioRef

    @property
    def instance(self) -> tuple[str, str]:
        return self.key[0], self.key[1]


# ----------------------------------------------------------------------------- values


def _verdict(r: Any) -> Verdict:
    return Verdict(getattr(r.verification.verdict, "value", r.verification.verdict))


def _episode_values(r: Any) -> dict[str, Any]:
    """Per-episode values of every comparison metric (PR 1B definitions, unchanged)."""
    t = episode_telemetry(r)
    v = _verdict(r)
    passed = v == Verdict.PASS
    reward = float(r.reward.final_reward)
    reg = _check_outcome(r, "regression")
    hidden = _check_pass(r, "hidden_security")
    tokens = float(t.prompt_tokens + t.completion_tokens) if t.usage_complete else None
    return {
        "verified_success": float(passed),
        "partial_success": float(v == Verdict.PARTIAL),
        "verifier_error_rate": float(v in (Verdict.ERROR, Verdict.INCONCLUSIVE)),
        "reward_positive_rate": float(reward >= REWARD_POSITIVE_THRESHOLD),
        "false_positive_reward_rate": float(
            reward >= REWARD_POSITIVE_THRESHOLD and not _truly_successful(r, None)
        ),
        "regression_failure_rate": float(reg == "fail"),
        "regression_failure_rate_executed": (float(reg == "fail"), float(reg in ("pass", "fail"))),
        "hidden_security_pass_rate": (0.0, 0.0) if hidden is None else (float(hidden), 1.0),
        "provider_failures.rate": float(t.provider_failure is not None),
        "mean_reward": reward,
        "tool_calls_per_attempt": float(r.tool_calls),
        "tool_calls_per_success": (float(r.tool_calls) if passed else 0.0, float(passed)),
        "mean_duration_seconds": float(r.duration_seconds),
        "cost.per_attempt_usd": t.cost,
        "cost.per_verified_success_usd": (t.cost, float(passed)),
        "tokens.per_attempt": tokens,
        "tokens.per_verified_success": (tokens, float(passed)),
        "paired_conditional.verified_success": float(passed),
        "provider_retries_per_episode": None if t.retries is None else float(t.retries),
        "_provider_failure": t.provider_failure is not None,
    }


# ----------------------------------------------------------------------------- matching


def _seed_of(r: Any) -> int | None:
    s = (getattr(r, "metadata", None) or {}).get("seed")
    return s if isinstance(s, int) and not isinstance(s, bool) else None


def _index(run: RunData, side: str, problems: _Problems) -> dict[Key, Any]:
    out: dict[Key, Any] = {}
    dups: set[Key] = set()
    for r in run.records:
        seed = _seed_of(r)
        if seed is None:
            problems.add("invalid_record", f"{side}: {r.task_id} has no integer episode seed")
            continue
        k = (str(r.task_id), str(r.task_version), seed)
        if k in out:
            dups.add(k)
        out[k] = r
    if dups:
        problems.add("duplicate_observation", f"{side}: {len(dups)} duplicated keys", dups)
    return out


def _resolve(
    index: dict[Key, Any], side: str, resolver: ScenarioResolver, problems: _Problems
) -> dict[Key, ScenarioRef]:
    refs: dict[Key, ScenarioRef] = {}
    for k, r in sorted(index.items()):
        try:
            refs[k] = resolver.resolve(r)
        except ScenarioError as exc:
            problems.add(exc.code, f"{side}: {exc.detail}", [k])
    return refs


def _effective_seeds(cfg: dict[str, Any]) -> list[int]:
    seeds = list(cfg.get("seeds") or [])
    if seeds:
        return seeds
    ps = (cfg.get("provider") or {}).get("seed")
    return [ps if ps is not None else 0]


def _flatten(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if k == "provider" and isinstance(v, dict):
            out.update({f"provider.{pk}": pv for pk, pv in v.items()})
        elif k != "seeds":
            out[k] = v
    out["seeds"] = _effective_seeds(cfg)
    return out


def _config_differences(
    b: dict[str, Any], c: dict[str, Any], varied: tuple[str, ...], problems: _Problems
) -> list[dict[str, Any]]:
    fb, fc = _flatten(b), _flatten(c)
    diffs = []
    for path in sorted(set(fb) | set(fc)):
        vb, vc = fb.get(path), fc.get(path)
        if vb == vc:
            continue
        root = path.split(".")[0]
        allowed = path in varied or root in varied
        if root in NEVER_VARIABLE or path in NEVER_VARIABLE:
            allowed = False
            problems.add("never_variable_config", f"{path}: {vb!r} != {vc!r}")
        elif path == PROVIDER_SEED:
            allowed = False
            problems.add("provider_seed_mismatch", f"provider.seed: {vb!r} != {vc!r}")
        elif not allowed:
            problems.add("config_mismatch", f"{path}: {vb!r} != {vc!r}")
        diffs.append({"path": path, "baseline": vb, "candidate": vc, "allowed": allowed})
    return diffs


def _run_checks(run: RunData, side: str, problems: _Problems) -> None:
    if not run.records:
        problems.add("empty_run", f"{side}: no episodes")
    if run.status != "completed":
        problems.add("run_not_completed", f"{side}: status {run.status}")
    if run.run_mode != "official" or run.leakage_ok is not True:
        problems.add(
            "run_not_official", f"{side}: run_mode={run.run_mode}, leakage_ok={run.leakage_ok}"
        )
    if run.ref.source == "bundle":
        problems.add("bundle_source", f"{side}: loaded from a reproducibility bundle")


def _record_checks(base: list[Any], cand: list[Any], problems: _Problems) -> None:
    versions: dict[str, set[str]] = defaultdict(set)
    for r in [*base, *cand]:
        versions[str(r.task_id)].add(str(r.task_version))
    bad = {t: v for t, v in versions.items() if len(v) > 1}
    if bad:
        problems.add("task_version_mismatch", f"{len(bad)} tasks with several versions: {bad}")
    verifiers = {
        (_value(r.verification.profile), str(r.verification.verifier_version))
        for r in [*base, *cand]
    }
    if len(verifiers) > 1:
        problems.add("verifier_mismatch", f"verifier (profile, version) differs: {verifiers}")
    non_auth = sum(
        1
        for r in [*base, *cand]
        if _value(r.verification.profile) != FULL_PROFILE or not r.verification.authoritative
    )
    if non_auth:
        problems.add("non_authoritative", f"{non_auth} records not {FULL_PROFILE}/authoritative")
    hashes = {str(r.reward.policy_hash) for r in [*base, *cand]}
    if len(hashes) > 1:
        problems.add("reward_policy_mismatch", f"{len(hashes)} distinct reward policy hashes")


def _scenario_checks(
    rb: dict[Key, ScenarioRef], rc: dict[Key, ScenarioRef], problems: _Problems
) -> None:
    by_task: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for k, ref in [*rb.items(), *rc.items()]:
        by_task[(k[0], k[1])].add((ref.scenario_id, ref.oracle_fingerprint))
    split = {t: s for t, s in by_task.items() if len(s) > 1}
    if split:
        problems.add("scenario_mismatch", f"{len(split)} tasks resolve to different scenarios")
    fp_to_scen: dict[str, set[str]] = defaultdict(set)
    scen_to_fp: dict[str, set[str]] = defaultdict(set)
    for ref in [*rb.values(), *rc.values()]:
        fp_to_scen[ref.oracle_fingerprint].add(ref.scenario_id)
        scen_to_fp[ref.scenario_id].add(ref.oracle_fingerprint)
    collisions = {fp[:12]: s for fp, s in fp_to_scen.items() if len(s) > 1}
    if collisions:
        problems.add("scenario_identity_collision", f"shared oracle fingerprints: {collisions}")
    multi = {s: len(f) for s, f in scen_to_fp.items() if len(f) > 1}
    if multi:
        problems.add("oracle_identity_mismatch", f"scenarios with several fingerprints: {multi}")
    identities = {
        sha256_json(ref.benchmark_identity) if ref.benchmark_identity else None
        for ref in [*rb.values(), *rc.values()]
    }
    if len(identities) > 1:
        problems.add("benchmark_identity_mismatch", "records carry different benchmark identities")


def _value(x: Any) -> str:
    return str(getattr(x, "value", x))


# ----------------------------------------------------------------------------- estimators


def _null_difference(unit: str, request: ComparisonRequest, note: str) -> dict[str, Any]:
    return {
        "estimate": None,
        "ci_low": None,
        "ci_high": None,
        "confidence": request.confidence,
        "samples": request.bootstrap_samples,
        "seed": request.bootstrap_seed,
        "method": None,
        "cluster_unit": unit,
        "n_clusters": 0,
        "n_observations": 0,
        "undefined_replicates": 0,
        "note": note,
    }


def _difference(boot: dict[str, Any], unit: str, request: ComparisonRequest) -> dict[str, Any]:
    return {
        "estimate": boot["estimate"],
        "ci_low": boot["ci_low"],
        "ci_high": boot["ci_high"],
        "confidence": request.confidence,
        "samples": request.bootstrap_samples,
        "seed": request.bootstrap_seed,
        "method": (
            f"{unit.replace('_', '-')} cluster percentile bootstrap (pooled paired difference)"
            if boot["method"] == CLUSTER_METHOD
            else boot["method"]
        ),
        "cluster_unit": unit,
        "n_clusters": boot["n_clusters"],
        "n_observations": boot["n_observations"],
        "undefined_replicates": boot.get("undefined_replicates", 0),
        "note": boot.get("note"),
    }


ClusterKey = Callable[[Pair], Any]


def _scenario_key(p: Pair) -> Any:
    return p.ref.scenario_id


def _instance_key(p: Pair) -> Any:
    return p.instance


def _mean_boot(
    rows: list[tuple[Pair, float, float]], key: ClusterKey, request: ComparisonRequest
) -> dict[str, Any]:
    groups: dict[Any, list[float]] = defaultdict(list)
    for p, b, c in rows:
        groups[key(p)].append(c - b)
    return cluster_bootstrap(
        [groups[g] for g in sorted(groups)],
        samples=request.bootstrap_samples,
        confidence=request.confidence,
        seed=request.bootstrap_seed,
    )


def _ratio_boot(
    rows: list[tuple[Pair, tuple[float, float], tuple[float, float]]],
    key: ClusterKey,
    request: ComparisonRequest,
) -> dict[str, Any]:
    sums: dict[Any, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for p, (nb, db), (nc, dc) in rows:
        s = sums[key(p)]
        s[0] += nc
        s[1] += dc
        s[2] += nb
        s[3] += db
    return paired_cluster_ratio_bootstrap(
        [(s[0], s[1], s[2], s[3]) for _, s in sorted(sums.items())],
        samples=request.bootstrap_samples,
        confidence=request.confidence,
        seed=request.bootstrap_seed,
    )


def _comparable(
    spec: cc.ComparisonMetricSpec,
    pairs: list[Pair],
    vals: dict[int, dict[str, Any]],
    reward_ok: bool,
) -> str | None:
    """None if comparable, else the reason."""
    when = spec.comparable_when
    b = [vals[id(p.base)] for p in pairs]
    c = [vals[id(p.cand)] for p in pairs]
    if "reward policy" in when and not reward_ok:
        return "reward policy hashes differ between arms"
    if "cost complete" in when:
        for arm, name in ((b, "baseline"), (c, "candidate")):
            costs = [v["cost.per_attempt_usd"] for v in arm]
            if any(x is None for x in costs) or _finite_sum([float(x) for x in costs]) is None:
                return f"{name} cost coverage is not complete (or the total overflows)"
    if "usage complete" in when:
        for arm, name in ((b, "baseline"), (c, "candidate")):
            if any(v["tokens.per_attempt"] is None for v in arm):
                return f"{name} token usage coverage is not complete"
    if "attempt counts" in when:
        for arm, name in ((b, "baseline"), (c, "candidate")):
            if any(v["provider_retries_per_episode"] is None for v in arm):
                return f"{name} attempt counts are not known for every episode"
    if "PASS" in when:
        for arm, name in ((b, "baseline"), (c, "candidate")):
            if not any(v["verified_success"] for v in arm):
                return f"{name} has no PASS episode"
    return None


def _direction(spec: cc.ComparisonMetricSpec, diff: float | None) -> str:
    if diff is None or diff == 0 or spec.direction == "neutral":
        return "none"
    good = diff > 0 if spec.direction == "higher" else diff < 0
    return "favourable" if good else "unfavourable"


def _interpret(
    spec: cc.ComparisonMetricSpec,
    diff: dict[str, Any],
    reason: str | None,
    official: bool,
    primary: bool,
) -> str:
    if reason is not None:
        return "not_comparable"
    if diff["ci_low"] is None or diff["ci_high"] is None:
        return "undefined"
    if not official:
        return "diagnostic_only"
    if not primary or spec.key != cc.PRIMARY_METRIC or spec.cls != "A":
        return "secondary"
    tier = cc.scenario_tier(diff["n_clusters"])
    if tier != "directional_eligible":
        return tier
    if diff["ci_low"] > 0:
        return "improvement" if spec.direction == "higher" else "regression"
    if diff["ci_high"] < 0:
        return "regression" if spec.direction == "higher" else "improvement"
    return "no_detectable_difference"


def _arm(n: float, d: float) -> dict[str, Any]:
    return {"estimate": (n / d) if d else None, "numerator": n, "denominator": d, "n": int(d)}


def _metric(
    spec: cc.ComparisonMetricSpec,
    pairs: list[Pair],
    vals: dict[int, dict[str, Any]],
    request: ComparisonRequest,
    reward_ok: bool,
    official: bool,
    primary: bool,
) -> dict[str, Any]:
    reason = _comparable(spec, pairs, vals, reward_ok)
    population = pairs
    if spec.key == "paired_conditional.verified_success":
        population = [
            p
            for p in pairs
            if not vals[id(p.base)]["_provider_failure"]
            and not vals[id(p.cand)]["_provider_failure"]
        ]
    out: dict[str, Any] = {
        "metric": spec.key,
        "class": spec.cls,
        "kind": spec.kind,
        "direction": spec.direction,
        "category": spec.category,
        "population": spec.population,
    }
    if spec.kind == "mean":
        rows = [
            (p, vals[id(p.base)][spec.key], vals[id(p.cand)][spec.key])
            for p in population
            if vals[id(p.base)][spec.key] is not None and vals[id(p.cand)][spec.key] is not None
        ]
        nb, nc, n = sum(r[1] for r in rows), sum(r[2] for r in rows), float(len(rows))
        base, cand = _arm(nb, n), _arm(nc, n)
        used = len(rows)
        if reason is None and rows:
            primary_boot = _mean_boot(rows, _scenario_key, request)
            sens_boot = _mean_boot(rows, _instance_key, request)
    else:
        rrows = [(p, vals[id(p.base)][spec.key], vals[id(p.cand)][spec.key]) for p in population]
        rrows = [r for r in rrows if r[1][0] is not None and r[2][0] is not None]
        base = _arm(sum(r[1][0] for r in rrows), sum(r[1][1] for r in rrows))
        cand = _arm(sum(r[2][0] for r in rrows), sum(r[2][1] for r in rrows))
        used = len(rrows)
        if reason is None and rrows:
            primary_boot = _ratio_boot(rrows, _scenario_key, request)
            sens_boot = _ratio_boot(rrows, _instance_key, request)
        rows = []
    if reason is None and used:
        diff = _difference(primary_boot, "structural_scenario", request)
        sens = _difference(sens_boot, "task_instance", request)
    else:
        note = reason or "no usable pairs"
        diff = _null_difference("structural_scenario", request, note)
        sens = _null_difference("task_instance", request, note)
    p_w = (diff["ci_high"] - diff["ci_low"]) if diff["ci_low"] is not None else None
    s_w = (sens["ci_high"] - sens["ci_low"]) if sens["ci_low"] is not None else None
    sens["width_ratio"] = (s_w / p_w) if p_w and s_w is not None else None
    sens["label"] = (
        "assumes task instances are independent; anti-conservative when instances share a "
        "structural scenario; never used for interpretation"
    )
    est = diff["estimate"]
    b_est = base["estimate"]
    relative = (
        est / b_est
        if est is not None and b_est is not None and b_est > 0 and spec.key not in SIGNED_METRICS
        else None
    )
    out.update(
        {
            "baseline": base,
            "candidate": cand,
            "difference": diff,
            "instance_sensitivity": sens,
            "relative_difference": relative,
            "coverage": {"pairs_used": used, "pairs_total": len(pairs)},
            "scenario_tier": cc.scenario_tier(diff["n_clusters"]),
            "interpretation": _interpret(spec, diff, reason, official, primary),
            "estimate_direction": _direction(spec, est),
            "not_comparable_reason": reason,
        }
    )
    return out


# ----------------------------------------------------------------------------- groups


def _minmax(xs: list[int]) -> dict[str, int | None]:
    return {"min": min(xs) if xs else None, "max": max(xs) if xs else None}


def _counts(pairs: list[Pair]) -> dict[str, Any]:
    inst_by_scen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    seeds_by_inst: dict[tuple[str, str], set[int]] = defaultdict(set)
    pairs_by_scen: Counter[str] = Counter()
    for p in pairs:
        inst_by_scen[p.ref.scenario_id].add(p.instance)
        seeds_by_inst[p.instance].add(p.key[2])
        pairs_by_scen[p.ref.scenario_id] += 1
    n = len(pairs)
    return {
        "n_pairs": n,
        "n_task_instances": len(seeds_by_inst),
        "n_structural_scenarios": len(inst_by_scen),
        "n_families": len({p.ref.family_id for p in pairs}),
        "instances_per_scenario": _minmax([len(v) for v in inst_by_scen.values()]),
        "seeds_per_instance": _minmax([len(v) for v in seeds_by_inst.values()]),
        "scenario_weights": {s: c / n for s, c in sorted(pairs_by_scen.items())},
    }


def _group(
    pairs: list[Pair],
    specs: list[cc.ComparisonMetricSpec],
    vals: dict[int, dict[str, Any]],
    request: ComparisonRequest,
    reward_ok: bool,
    official: bool,
    primary: bool,
    note: str | None,
) -> dict[str, Any]:
    counts = _counts(pairs)
    return {
        "role": "primary" if primary else "secondary",
        "in_distribution_note": note,
        "counts": counts,
        "scenario_tier": cc.scenario_tier(counts["n_structural_scenarios"]),
        "metrics": {
            s.key: _metric(s, pairs, vals, request, reward_ok, official, primary) for s in specs
        },
    }


def _change(diffs: list[float]) -> str:
    if all(d == 0 for d in diffs):
        return "unchanged"
    if all(d >= 0 for d in diffs):
        return "improved"
    if all(d <= 0 for d in diffs):
        return "regressed"
    return "mixed"


def _transitions(pairs: list[Pair]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(f"{_verdict(p.base).value}->{_verdict(p.cand).value}" for p in pairs).items()
        )
    )


def _vs_block(pairs: list[Pair], vals: dict[int, dict[str, Any]]) -> dict[str, Any]:
    b = [vals[id(p.base)]["verified_success"] for p in pairs]
    c = [vals[id(p.cand)]["verified_success"] for p in pairs]
    bm, cm = sum(b) / len(b), sum(c) / len(c)
    return {"verified_success": {"baseline_mean": bm, "candidate_mean": cm, "difference": cm - bm}}


# ----------------------------------------------------------------------------- run blocks


def _run_block(run: RunData) -> dict[str, Any]:
    provider = run.config.get("provider") or {}
    first = run.records[0] if run.records else None
    reward = getattr(first, "reward", None)
    ver = getattr(first, "verification", None)
    return {
        "evaluation_id": str(run.ref.evaluation_id),
        "source": run.ref.source,
        "bundle_dir": run.ref.bundle_dir,
        "config_name": run.ref.config_name,
        "name": run.name or run.config.get("name"),
        "config_hash": run.config_hash,
        "run_mode": run.run_mode,
        "official": run.run_mode == "official" and run.leakage_ok is True,
        "status": run.status,
        "provider_kind": provider.get("kind"),
        "model_id": provider.get("model_id"),
        "policy": provider.get("policy"),
        "verifier_profile": _value(ver.profile) if ver is not None else None,
        "verifier_version": str(ver.verifier_version) if ver is not None else None,
        "reward_policy": {
            "id": getattr(reward, "policy_id", None),
            "version": getattr(reward, "policy_version", None),
            "hash": getattr(reward, "policy_hash", None),
        },
        "n_episodes": len(run.records),
    }


def _digest(r: Any) -> str:
    return sha256_json(
        [
            str(r.task_id),
            str(r.task_version),
            _seed_of(r),
            str(getattr(r, "rollout_id", "")),
            _verdict(r).value,
            float(r.reward.final_reward),
        ]
    )


def _evidence_source(*runs: RunData) -> str:
    scripted = any(
        (run.config.get("provider") or {}).get("kind", "demo") == "demo"
        or any((getattr(r, "metadata", {}) or {}).get("demo_provider") for r in run.records)
        for run in runs
    )
    return "pipeline_validation" if scripted else "model_comparison"


def _banner(tier: str, source: str) -> str:
    text = {
        "diagnostic": "Evidence tier: diagnostic. This comparison is not an official matched "
        "comparison and supports no directional claim.",
        "no_primary_endpoint": "Evidence tier: no_primary_endpoint. No private_test pairs: the "
        "held-out primary endpoint is missing; no directional claim is supported and pooled "
        "results are not substituted.",
        "insufficient_scenarios": "Evidence tier: insufficient_scenarios. Fewer than 10 "
        "structural scenarios in the primary endpoint; no directional claim is supported.",
        "exploratory_only": "Evidence tier: exploratory_only. This comparison does not support a "
        "directional improvement or regression claim.",
        "directional_eligible": "Evidence tier: directional_eligible. A directional statement "
        "requires the primary interval to exclude 0; 20 scenarios is an eligibility gate, not "
        "proof of validity or generalisation.",
    }[tier]
    if source == "pipeline_validation":
        text += " Scripted provider: pipeline validation, not learned-model evidence."
    return text


# ----------------------------------------------------------------------------- compare


def compare(
    baseline: RunData,
    candidate: RunData,
    request: ComparisonRequest,
    resolver: ScenarioResolver,
) -> dict[str, Any]:
    """Compare two runs. Raises ``ComparisonRefused`` when matching refuses the comparison."""
    specs = list(cc.COMPARABLE_METRICS)
    if request.metrics is not None:
        unknown = set(request.metrics) - set(cc.METRIC_SPECS)
        if unknown:
            raise ValueError(f"unknown comparison metrics: {sorted(unknown)}")
        specs = [s for s in specs if s.key in request.metrics]
    strict = request.coverage_policy == "strict"
    problems = _Problems()
    _run_checks(baseline, "baseline", problems)
    _run_checks(candidate, "candidate", problems)
    ib = _index(baseline, "baseline", problems)
    ic = _index(candidate, "candidate", problems)
    _record_checks(list(ib.values()), list(ic.values()), problems)
    config_diffs = _config_differences(
        baseline.config, candidate.config, request.varied_factors, problems
    )
    rb = _resolve(ib, "baseline", resolver, problems)
    rc = _resolve(ic, "candidate", resolver, problems)
    _scenario_checks(rb, rc, problems)
    missing, extra = sorted(set(ib) - set(ic)), sorted(set(ic) - set(ib))
    excluded: list[dict[str, Any]] = []
    if missing or extra:
        problems.add(
            "coverage_mismatch",
            f"{len(missing)} keys only in baseline, {len(extra)} only in candidate",
            [*missing, *extra],
        )
        if not strict:
            excluded = [
                {"key": list(k), "side": "baseline_only", "reason": "no candidate episode"}
                for k in missing
            ] + [
                {"key": list(k), "side": "candidate_only", "reason": "no baseline episode"}
                for k in extra
            ]
    pairs = [Pair(k, ib[k], ic[k], rb[k]) for k in sorted(set(ib) & set(ic)) if k in rb and k in rc]
    severities = problems.severities()
    refused = "hard" in severities or (strict and "strict" in severities)
    status = "refused" if refused else ("matched" if strict else "diagnostic")
    official = status == "matched" and "not_official" not in severities
    primary_pairs = [p for p in pairs if p.ref.split == cc.PRIMARY_SPLIT]
    if request.report_tier == "final" and not refused:
        n_primary = len({p.ref.scenario_id for p in primary_pairs})
        if not official:
            problems.add("final_tier_requirement", "final report needs an official comparison")
        if n_primary < cc.INSUFFICIENT_BELOW:
            problems.add(
                "final_tier_requirement",
                f"final report needs >= {cc.INSUFFICIENT_BELOW} primary (private_test) "
                f"structural scenarios; found {n_primary}",
            )
        if "final_tier_requirement" in problems.codes():
            refused, status, official = True, "refused", False

    counts = _counts(pairs) if pairs else _counts([])
    matching = {
        "status": status,
        "official": official,
        "coverage_policy": request.coverage_policy,
        "problems": problems.as_list(),
        "excluded": excluded,
        "config_differences": config_diffs,
        "n_pairs": counts["n_pairs"],
        "n_task_instances": counts["n_task_instances"],
        "n_structural_scenarios": counts["n_structural_scenarios"],
        "n_families": counts["n_families"],
    }
    runs = {"baseline": _run_block(baseline), "candidate": _run_block(candidate)}
    if refused:
        raise ComparisonRefused(
            {"request": request.model_dump(mode="json"), **runs, "matching": matching}
        )

    vals: dict[int, dict[str, Any]] = {}
    for p in pairs:
        vals[id(p.base)] = _episode_values(p.base)
        vals[id(p.cand)] = _episode_values(p.cand)
    reward_ok = "reward_policy_mismatch" not in problems.codes()
    legacy = any(p.ref.source == "legacy_registry" for p in pairs)

    def build(subset: list[Pair], primary: bool, note: str | None = None) -> dict[str, Any]:
        return _group(subset, specs, vals, request, reward_ok, official, primary, note)

    def by(attr: Callable[[Pair], str]) -> dict[str, list[Pair]]:
        out: dict[str, list[Pair]] = defaultdict(list)
        for p in pairs:
            out[attr(p)].append(p)
        return dict(sorted(out.items()))

    by_split = {
        s: build(
            ps,
            primary=s == cc.PRIMARY_SPLIT,
            note=cc.IN_DISTRIBUTION_NOTE if s == "public_test" and legacy else None,
        )
        for s, ps in by(lambda p: p.ref.split).items()
    }
    groups = {
        "overall": build(pairs, primary=False),
        "by_split": by_split,
        "by_family": {f: build(ps, False) for f, ps in by(lambda p: p.ref.family_id).items()},
        "by_difficulty": {d: build(ps, False) for d, ps in by(lambda p: p.ref.difficulty).items()},
    }
    if primary_pairs:
        primary_endpoint = {
            "status": "available",
            "group": cc.PRIMARY_GROUP,
            "metric": cc.PRIMARY_METRIC,
            "reason": None,
        }
    else:
        primary_endpoint = {
            "status": "unavailable",
            "group": None,
            "metric": cc.PRIMARY_METRIC,
            "reason": "no private_test pairs: no held-out primary endpoint",
        }
    if not official:
        evidence_tier = "diagnostic"
    elif not primary_pairs:
        evidence_tier = "no_primary_endpoint"
    else:
        pm = by_split[cc.PRIMARY_SPLIT]["metrics"].get(cc.PRIMARY_METRIC)
        n_cl = pm["difference"]["n_clusters"] if pm else 0
        evidence_tier = cc.scenario_tier(n_cl)
    source = _evidence_source(baseline, candidate)

    by_scen: dict[str, list[Pair]] = by(lambda p: p.ref.scenario_id)
    from traceforge.modules.evaluation.scenarios import LEGACY_SCENARIOS

    reasons = {e.scenario_id: e.reason for e in LEGACY_SCENARIOS.values()}
    per_scenario = []
    for sid, ps in by_scen.items():
        diffs = [
            vals[id(p.cand)]["verified_success"] - vals[id(p.base)]["verified_success"] for p in ps
        ]
        ref = ps[0].ref
        per_scenario.append(
            {
                "scenario_id": sid,
                "family": ref.family_id,
                "templates": sorted({p.ref.template_id for p in ps}),
                "oracle_fingerprint": ref.oracle_fingerprint,
                "source": ref.source,
                "reason": reasons.get(sid) if ref.source == "legacy_registry" else None,
                "task_instances": sorted({f"{p.key[0]}@{p.key[1]}" for p in ps}),
                "n_pairs": len(ps),
                "verdict_transitions": _transitions(ps),
                "metrics": _vs_block(ps, vals),
                "change": _change(diffs),
            }
        )
    by_inst: dict[tuple[str, str], list[Pair]] = defaultdict(list)
    for p in pairs:
        by_inst[p.instance].append(p)
    per_task = []
    for (tid, tver), ps in sorted(by_inst.items()):
        ref = ps[0].ref
        diffs = [
            vals[id(p.cand)]["verified_success"] - vals[id(p.base)]["verified_success"] for p in ps
        ]
        per_task.append(
            {
                "task_id": tid,
                "task_version": tver,
                "scenario_id": ref.scenario_id,
                "family": ref.family_id,
                "template": ref.template_id,
                "split": ref.split,
                "difficulty": ref.difficulty,
                "n_pairs": len(ps),
                "seeds": sorted(p.key[2] for p in ps),
                "verdict_transitions": _transitions(ps),
                "metrics": _vs_block(ps, vals),
                "change": _change(diffs),
            }
        )

    sb = summarize([p.base for p in pairs], bootstrap_samples=100)
    sc = summarize([p.cand for p in pairs], bootstrap_samples=100)
    latency = {}
    for k in cc.DESCRIPTIVE_LATENCY:
        lb, lc = sb["latency"][k], sc["latency"][k]
        latency[k] = {
            "baseline": {q: lb[q] for q in ("n", "p50", "p90", "p95", "max")},
            "candidate": {q: lc[q] for q in ("n", "p50", "p90", "p95", "max")},
            "difference": {
                q: (lc[q] - lb[q]) if lc[q] is not None and lb[q] is not None else None
                for q in ("p50", "p90", "p95")
            },
        }

    def cost_arm(s: dict[str, Any]) -> dict[str, Any]:
        c = s["cost"]
        return {
            "status": c["status"],
            "covered": c["episodes_with_known_cost"],
            "n": c["n_episodes"],
            "total": c["total_usd"],
            "lower_bound": c["lower_bound_usd"],
        }

    def usage_arm(s: dict[str, Any]) -> dict[str, Any]:
        t = s["tokens"]
        n, cov = t["n_episodes"], t["episodes_with_complete_usage"]
        status = (
            "no_episodes" if n == 0 else "complete" if cov == n else "partial" if cov else "unknown"
        )
        return {"status": status, "covered": cov, "n": n, "total": t["total"], "lower_bound": None}

    cb, cc_ = cost_arm(sb), cost_arm(sc)
    ub, uc = usage_arm(sb), usage_arm(sc)
    cost_ok = cb["status"] == "complete" and cc_["status"] == "complete"
    usage_ok = ub["status"] == "complete" and uc["status"] == "complete"

    warnings: list[dict[str, Any]] = [
        {"code": p["code"], "detail": p["detail"]}
        for p in problems.as_list()
        if p["severity"] in ("strict", "not_official")
    ]
    pf_b = groups["overall"]["metrics"].get("provider_failures.rate")
    if pf_b and pf_b["baseline"]["estimate"] != pf_b["candidate"]["estimate"]:
        warnings.append(
            {
                "code": "provider_failure_confounding",
                "detail": "provider failure rates differ between arms; verified_success "
                "differences are confounded with infrastructure (see "
                "paired_conditional.verified_success, diagnostic)",
            }
        )
    undefined = sorted(
        {
            m["metric"]
            for g in [groups["overall"], *by_split.values()]
            for m in g["metrics"].values()
            if m["difference"]["undefined_replicates"]
        }
    )
    if undefined:
        warnings.append(
            {
                "code": "undefined_ratio_replicates",
                "detail": f"ratio intervals undefined (zero-denominator replicates): {undefined}",
            }
        )
    seeds_matched = (baseline.config.get("provider") or {}).get("seed") == (
        candidate.config.get("provider") or {}
    ).get("seed")
    first = pairs[0].base if pairs else None
    reward = getattr(first, "reward", None)
    ver = getattr(first, "verification", None)
    doc: dict[str, Any] = {
        "comparison_schema_version": cc.COMPARISON_SCHEMA_VERSION,
        "comparison_fingerprint": sha256_json(
            {
                "request": request.model_dump(mode="json"),
                "baseline": sorted(_digest(r) for r in baseline.records),
                "candidate": sorted(_digest(r) for r in candidate.records),
            }
        ),
        "request": request.model_dump(mode="json"),
        "report_tier": request.report_tier,
        "evidence_tier": evidence_tier,
        "evidence_banner": _banner(evidence_tier, source),
        "evidence_source": source,
        "learned_model_evidence": source == "model_comparison",
        "seeds": {
            "episode_seeds": sorted({p.key[2] for p in pairs}),
            "provider_seed": {
                "baseline": (baseline.config.get("provider") or {}).get("seed"),
                "candidate": (candidate.config.get("provider") or {}).get("seed"),
                "matched": seeds_matched,
            },
            "bootstrap_seed": request.bootstrap_seed,
            "provider_seed_note": cc.PROVIDER_SEED_NOTE,
        },
        **runs,
        "matching": matching,
        "protocol": {
            "task_versions": sorted({p.key[1] for p in pairs}),
            "scenario_source": "+".join(sorted({p.ref.source for p in pairs})),
            "scenario_registry_version": SCENARIO_REGISTRY_VERSION,
            "oracle_normalisation_version": ORACLE_NORMALISATION_VERSION,
            "benchmark_identity": next(
                (p.ref.benchmark_identity for p in pairs if p.ref.benchmark_identity), None
            ),
            "split_policy": {"id": SPLIT_POLICY, "version": SPLIT_POLICY_VERSION}
            if legacy
            else None,
            "verifier": {
                "profile": _value(ver.profile) if ver is not None else None,
                "version": str(ver.verifier_version) if ver is not None else None,
                "authoritative": "non_authoritative" not in problems.codes(),
            },
            "reward_policy": {
                "id": getattr(reward, "policy_id", None),
                "version": getattr(reward, "policy_version", None),
                "hash": getattr(reward, "policy_hash", None) if reward_ok else None,
            },
            "reward_threshold": REWARD_POSITIVE_THRESHOLD,
        },
        "primary_endpoint": primary_endpoint,
        "groups": groups,
        "per_scenario": per_scenario,
        "per_task": per_task,
        "latency": latency,
        "cost_coverage": {
            "baseline": cb,
            "candidate": cc_,
            "comparable": cost_ok,
            "reason": None
            if cost_ok
            else "cost coverage is not complete in both arms; lower bounds are never differenced",
        },
        "usage_coverage": {
            "baseline": ub,
            "candidate": uc,
            "comparable": usage_ok,
            "reason": None if usage_ok else "token usage is not complete in both arms",
        },
        "statistics": {
            "primary_cluster_unit": "structural_scenario",
            "secondary_cluster_unit": "task_instance",
            "estimands": {
                "mean": "sum of paired differences / usable pairs (difference of pooled means)",
                "ratio": "sum(num_C)/sum(den_C) - sum(num_B)/sum(den_B)",
                "paired_conditional": "mean paired difference over pairs where neither arm had "
                "a provider failure (selected population; diagnostic)",
            },
            "method": f"{CLUSTER_METHOD}; paired ratio bootstrap with one shared draw",
            "samples": request.bootstrap_samples,
            "bootstrap_seed": request.bootstrap_seed,
            "confidence": request.confidence,
            "thresholds": {
                "insufficient_below": cc.INSUFFICIENT_BELOW,
                "exploratory_below": cc.EXPLORATORY_BELOW,
            },
            "eligibility_note": cc.ELIGIBILITY_NOTE,
        },
        "report": {
            "tier": request.report_tier,
            "bootstrap_samples": request.bootstrap_samples,
            "final_min_bootstrap_samples": 10_000,
            "final_min_primary_scenarios": cc.INSUFFICIENT_BELOW,
            "note": "final: official strict comparison, v3_full on both arms, matching provider "
            "seeds, >= 10,000 resamples and >= 10 primary scenarios",
        },
        "warnings": warnings,
        "limitations": list(cc.LIMITATIONS),
    }
    if request.include_pairs:
        doc["pairs"] = [
            {
                "key": list(p.key),
                "scenario_id": p.ref.scenario_id,
                "family": p.ref.family_id,
                "template": p.ref.template_id,
                "split": p.ref.split,
                "difficulty": p.ref.difficulty,
                "baseline_rollout_id": str(getattr(p.base, "rollout_id", "")),
                "candidate_rollout_id": str(getattr(p.cand, "rollout_id", "")),
                "values": {
                    s.key: {
                        "baseline": vals[id(p.base)][s.key],
                        "candidate": vals[id(p.cand)][s.key],
                    }
                    for s in specs
                    if s.kind == "mean"
                },
            }
            for p in pairs
        ]
    return doc
