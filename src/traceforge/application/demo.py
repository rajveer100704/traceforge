"""End-to-end lifecycle smoke test (`traceforge demo`). Requires no GPU, API key or Docker.

Every step asserts a real, computed property; failures are collected and reported.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from traceforge.contracts.datasets import CurationLevel
from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig
from traceforge.contracts.tasks import Split
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.contamination import check_task_splits
from traceforge.modules.evaluation.experiments import pick_tasks
from traceforge.modules.tasks.registry import validate_registry

if TYPE_CHECKING:
    from traceforge.application.container import Platform

EXAMPLE_TRACES = (
    Path(__file__).resolve().parents[3] / "examples" / "traces" / "synthetic_traces.jsonl"
)
CORPUS_POLICIES = ("reference", "noop", "false_claim", "visible_overfit")


async def run_demo(p: Platform, profile: str = "local") -> dict[str, Any]:
    t0 = time.perf_counter()
    failures: list[str] = []
    steps: dict[str, Any] = {}

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)

    # 1. tasks
    problems = validate_registry(p.registry)
    leakage = check_task_splits(p.registry.list())
    steps["tasks"] = {
        "registered": len(p.registry),
        "problems": problems,
        "leakage_ok": leakage.ok,
        "by_split": leakage.counts_by_split,
    }
    check(len(p.registry) >= 30 and not problems and leakage.ok, "task registry invalid or leaking")

    # 2. ingestion of example traces
    if EXAMPLE_TRACES.exists():
        ing = p.ingestion.ingest_bytes(EXAMPLE_TRACES.name, EXAMPLE_TRACES.read_bytes())
        steps["ingestion"] = {
            "stored": len(ing.stored_trajectory_ids),
            "quarantined": ing.quarantined,
            "report": ing.report.model_dump(mode="json"),
        }
        check(ing.report.total_records > 0, "no example records ingested")
        check(
            ing.report.invalid + ing.report.rejected > 0
            or ing.report.duplicates > 0
            or (ing.quarantined == 0 and len(ing.stored_trajectory_ids) == 0),
            "defective example records were not caught",
        )
    else:
        failures.append(f"example traces missing at {EXAMPLE_TRACES}")

    # 3. verified corpus: rollouts on training tasks with reference and adversarial demo policies
    corpus: list[dict[str, Any]] = []
    for bundle in pick_tasks(p.registry, per_template=1, splits=(Split.TRAIN,)):
        for policy in CORPUS_POLICIES:
            art = await p.executor.run(
                bundle, ProviderConfig(policy=policy), correlation_id=f"demo:{policy}"
            )
            p.rollouts.persist(art, "verified-v1")
            corpus.append(
                {
                    "task_id": bundle.spec.task_id,
                    "family": bundle.oracle.family_id,
                    "policy": policy,
                    "verdict": art.summary.verification.verdict.value,
                    "reward": art.summary.reward.final_reward,
                    "steps_recorded": len(art.trajectory.steps),
                    "tool_calls": art.summary.tool_calls,
                    "verification_id": str(art.summary.verification.verification_id),
                    "evidence_ok": (c := art.summary.verification.check("evidence_quality"))
                    is not None
                    and c.status.value == "pass",
                }
            )
    steps["corpus_rollouts"] = corpus
    for row in corpus:
        if row["policy"] == "reference":
            check(
                row["verdict"] == Verdict.PASS.value and row["evidence_ok"],
                f"reference failed (or produced no valid evidence) on {row['task_id']}",
            )
        else:
            check(
                row["reward"] == 0.0,
                f"adversarial policy {row['policy']} rewarded on {row['task_id']}",
            )
        check(
            row["steps_recorded"] >= row["tool_calls"] + 2,
            f"steps not fully recorded for {row['task_id']}",
        )
    check(
        {r["family"] for r in corpus} == {"sql_injection", "access_control", "ssrf"},
        "not all families ran",
    )

    # 4. datasets (D3 verified successes, D4 + informative failures); rebuild must be idempotent
    d3 = p.datasets.build(
        "demo-sft", level=CurationLevel.D3_VERIFIED_SUCCESS, source_prefix="rollout:"
    )
    d3_again = p.datasets.build(
        "demo-sft", level=CurationLevel.D3_VERIFIED_SUCCESS, source_prefix="rollout:"
    )
    d4 = p.datasets.build(
        "demo-sft-d4", level=CurationLevel.D4_VERIFIED_PLUS_INFORMATIVE, source_prefix="rollout:"
    )
    rows_d3 = sum(f["num_rows"] for f in d3["manifest"]["partitions"])
    steps["datasets"] = {
        "d3": {
            "version": d3["manifest"]["version"],
            "rows": rows_d3,
            "counts_by_outcome": d3["manifest"]["counts_by_outcome"],
        },
        "d4": {
            "version": d4["manifest"]["version"],
            "rows": sum(f["num_rows"] for f in d4["manifest"]["partitions"]),
            "counts_by_outcome": d4["manifest"]["counts_by_outcome"],
        },
        "rebuild_same_version": d3_again["dataset_version_id"] == d3["dataset_version_id"],
    }
    check(rows_d3 > 0, "D3 dataset is empty")
    check(
        set(d3["manifest"]["counts_by_outcome"]) <= {"SUCCESS"}, "D3 contains non-success outcomes"
    )
    check(steps["datasets"]["rebuild_same_version"], "dataset rebuild was not idempotent")

    # 5. benchmark on held-out splits (all three families)
    cfg = BenchmarkConfig(
        name=f"demo-{profile}",
        splits=(Split.PUBLIC_TEST, Split.PRIVATE_TEST),
        provider=ProviderConfig(policy="reference"),
        sandbox=p.sandbox.name,
    )
    result, paths = await p.evaluations.run(cfg)
    overall = result.metrics["overall"]
    steps["benchmark"] = {
        "evaluation_id": str(result.benchmark_id),
        "n": overall["n"],
        "verified_success": overall["verified_success"],
        "by_family": {
            k: v["verified_success"]["rate"] for k, v in result.metrics["by_family"].items()
        },
        "by_split": {
            k: v["verified_success"]["rate"] for k, v in result.metrics["by_split"].items()
        },
        **paths,
    }
    check(
        set(result.metrics["by_family"]) == {"sql_injection", "access_control", "ssrf"},
        "benchmark did not cover all families",
    )
    check(
        overall["verified_success"]["successes"] == overall["n"],
        "reference policy failed held-out tasks",
    )
    check(
        Path(paths["report_json"]).exists() and Path(paths["report_md"]).exists(),
        "reports not written",
    )

    # 6. reward recomputation from immutable verification artifacts
    rc = p.rewards.recompute(
        __import__("uuid").UUID(corpus[0]["verification_id"]), "verified-v1", None
    )
    steps["reward_recompute"] = {
        "verification_id": corpus[0]["verification_id"],
        "matches_stored": rc["matches_stored"],
    }
    check(rc["matches_stored"] is True, "recomputed reward differs from stored reward")

    # 7. sandbox cleanup
    live: list[str] = getattr(p.sandbox, "live_sandboxes", lambda: [])()
    leftovers = sorted(x.name for x in (p.settings.data_dir / "sandboxes").glob("sbx-*"))
    steps["cleanup"] = {"live_sandboxes": live, "leftover_dirs": leftovers}
    check(not live and not leftovers, "sandboxes were not cleaned up")
    steps["isolation"] = {
        "sandbox_provider": p.sandbox.name,
        "isolation": p.sandbox.isolation,
        "docker_integration_executed": p.sandbox.name == "docker",
    }

    summary = {
        "passed": not failures,
        "failures": failures,
        "profile": profile,
        "duration_seconds": round(time.perf_counter() - t0, 2),
        "steps": steps,
        "note": "demo_provider is a deterministic scripted policy, not a learned model",
    }
    out_dir = p.settings.reports_dir / f"demo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "demo_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    summary["summary_path"] = str(out_dir / "demo_summary.json")
    return summary
