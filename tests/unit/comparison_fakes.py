"""Builders for paired-comparison tests (records, runs, a static scenario resolver)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from tests.unit.metric_fakes import ep
from traceforge.contracts.comparison import ComparisonRequest, RunRef
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.comparison import RunData, compare
from traceforge.modules.evaluation.scenarios import ScenarioError, ScenarioRef
from traceforge.modules.tasks.registry import TaskRegistry


def cep(
    task: str = "t1",
    verdict: Verdict = Verdict.PASS,
    *,
    seed: int = 0,
    version: str = "1.1.0",
    profile: str = "v3_full",
    authoritative: bool = True,
    policy_hash: str = "reward-hash",
    **kw: Any,
) -> Any:
    r = ep(task, verdict, seed=seed, authoritative=authoritative, **kw)
    r.task_version = version
    r.environment_ref = None
    r.verification.profile = profile
    r.verification.verifier_version = "1.1.0+engine1.1.0"
    r.reward.policy_id, r.reward.policy_version, r.reward.policy_hash = (
        "verified-v1",
        "1",
        policy_hash,
    )
    for k in ("family", "split", "difficulty"):  # identity comes from the resolver
        r.metadata.pop(k, None)
    return r


def real(registry: TaskRegistry, task_id: str, verdict: Verdict = Verdict.PASS, **kw: Any) -> Any:
    """A record whose identity matches the task registry (for legacy-resolver tests)."""
    version = kw.pop("version", "1.1.0")
    b = registry.bundle(task_id, version)
    r = cep(task_id, verdict, version=version, **kw)
    r.environment_ref = b.spec.environment_ref
    r.metadata.update(
        {
            "family": b.oracle.family_id,
            "template": b.oracle.template_id,
            "split": b.oracle.split.value,
        }
    )
    return r


class StaticResolver:
    """task_id -> (scenario, family, template, split, difficulty); fingerprint from scenario."""

    def __init__(self, table: dict[str, tuple[str, str, str, str, str]]) -> None:
        self.table = table

    def resolve(self, record: Any) -> ScenarioRef:
        if record.task_id not in self.table:
            raise ScenarioError("unknown_task", record.task_id)
        s, f, t, split, d = self.table[record.task_id]
        return ScenarioRef(s, f, t, split, d, f"fp-{s}", "legacy_registry")


def config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": "bench",
        "splits": ["public_test", "private_test"],
        "families": None,
        "max_tasks_per_family": None,
        "provider": {"kind": "openai_compatible", "model_id": "m", "seed": None},
        "sandbox": "local",
        "reward_policy": "verified-v1",
        "verifier_profile": "v3_full",
        "seed": 0,
        "bootstrap_samples": 2000,
        "confidence": 0.95,
        "seeds": [0, 1],
        "run_mode": "official",
    }
    for k, v in overrides.items():
        if k.startswith("provider."):
            cfg["provider"] = {**cfg["provider"], k.split(".", 1)[1]: v}
        else:
            cfg[k] = v
    return cfg


def run(records: Iterable[Any], cfg: dict[str, Any] | None = None, **kw: Any) -> RunData:
    return RunData(
        ref=kw.pop("ref", None) or RunRef(evaluation_id=uuid.uuid4()),
        config=cfg or config(),
        records=list(records),
        **kw,
    )


def request(**kw: Any) -> ComparisonRequest:
    base = {
        "baseline": {"evaluation_id": str(uuid.uuid4())},
        "candidate": {"evaluation_id": str(uuid.uuid4())},
        "bootstrap_samples": 200,
    }
    return ComparisonRequest.model_validate({**base, **kw})


# ------------------------------------------------------------------- golden fixture
# 4 scenarios x 2 instances x 2 seeds = 16 private_test pairs. verified_success differences:
# A [1,1,1,1]  B [1,0,0,0]  C [0,0,0,0]  D [0,0,-1,0]   ->  delta = 4/16 = 0.25
GOLDEN_DIFFS = {"A": [1, 1, 1, 1], "B": [1, 0, 0, 0], "C": [0, 0, 0, 0], "D": [0, 0, -1, 0]}
_BOTH_PASS = {"C"}


def golden_table() -> dict[str, tuple[str, str, str, str, str]]:
    return {
        f"{s.lower()}{i}": (s, f"fam{s}", "t", "private_test", "hard")
        for s in GOLDEN_DIFFS
        for i in (1, 2)
    }


def golden_runs(copies: int = 1) -> tuple[RunData, RunData]:
    """Baseline and candidate runs of the golden fixture. ``copies`` > 1 replicates every pair
    as extra seeds (uniform duplication)."""
    base, cand = [], []
    for s, diffs in GOLDEN_DIFFS.items():
        for j, d in enumerate(diffs):
            task = f"{s.lower()}{1 + j // 2}"
            for c in range(copies):
                seed = (j % 2) + 2 * c
                if s in _BOTH_PASS:
                    vb, vc = Verdict.PASS, Verdict.PASS
                elif d == 1:
                    vb, vc = Verdict.FAIL, Verdict.PASS
                elif d == -1:
                    vb, vc = Verdict.PASS, Verdict.FAIL
                else:
                    vb, vc = Verdict.FAIL, Verdict.FAIL
                base.append(cep(task, vb, seed=seed))
                cand.append(cep(task, vc, seed=seed))
    seeds = sorted({r.metadata["seed"] for r in base})
    return run(base, config(seeds=seeds)), run(cand, config(seeds=seeds))


def golden_compare(copies: int = 1, **kw: Any) -> dict[str, Any]:
    b, c = golden_runs(copies)
    return compare(b, c, request(**kw), StaticResolver(golden_table()))
