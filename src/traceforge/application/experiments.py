"""Experiment orchestration (A–E). Writes JSON + Markdown; unavailable parts are marked, not faked."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from traceforge.contracts.experiments import BenchmarkConfig, ExperimentSpec, ProviderConfig
from traceforge.contracts.tasks import Split
from traceforge.modules.evaluation import experiments as ex
from traceforge.modules.training.capabilities import detect

if TYPE_CHECKING:
    from traceforge.application.container import Platform

NAMES = {
    "A": "trajectory_quality",
    "B": "reward_hacking",
    "C": "sft_vs_grpo",
    "D": "generalization",
    "E": "robustness",
}


def _write(out_dir: Path, stem: str, doc: dict[str, Any], md: str) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(
        json.dumps(doc, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8", newline="\n")
    return {"json": str(out_dir / f"{stem}.json"), "markdown": str(out_dir / f"{stem}.md")}


def run_experiment(p: Platform, name: str, out_dir: Path | None = None) -> dict[str, Any]:
    if name not in NAMES:
        raise ValueError(f"unknown experiment {name}; choose from {sorted(NAMES)}")
    kind = NAMES[name]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_dir or p.settings.reports_dir / "experiments" / f"{name}_{kind}_{stamp}"
    experiment_id = p.experiments.create(ExperimentSpec(name=f"experiment-{name}", kind=kind))
    if name == "B":
        doc = asyncio.run(ex.run_reward_hacking(p.executor, p.registry, per_template=1))
        md = ex.hacking_markdown(doc)
    elif name == "E":
        doc = ex.run_verifier_robustness(p.sandbox, p.registry, p.policies)
        md = ex.robustness_markdown(doc)
    elif name == "A":
        policies = (
            "reference",
            "noop",
            "false_claim",
            "visible_overfit",
            "break_functionality",
            "malformed_then_reference",
        )
        trajectories = asyncio.run(
            ex.collect_verified_corpus(p.executor, p.registry, policies, per_template=1)
        )
        caps = detect()
        sft = (
            "NOT EXECUTED: downstream SFT comparison across D1–D4 requires a real base model; only "
            f"cpu-test integration is available here (sft mode: {caps.modes['sft'].reason})"
        )
        task_split = {b.spec.task_id: b.oracle.split.value for b in p.registry.list()}
        from traceforge.application.demo import EXAMPLE_TRACES
        from traceforge.modules.trace_processing.ingestion import IngestionPipeline, split_jsonl
        from traceforge.modules.trace_processing.quality import QualityEngine

        parsed = IngestionPipeline(QualityEngine()).run(
            EXAMPLE_TRACES.name, split_jsonl(EXAMPLE_TRACES.read_bytes())
        )
        imported = [t for t, _a, _r in parsed.trajectories]
        doc = ex.run_trajectory_quality(trajectories, task_split, sft, imported)
        md = ex.trajectory_quality_markdown(doc)
    elif name == "D":
        cfg = BenchmarkConfig(
            name="exp-D-generalization",
            splits=(Split.VALIDATION, Split.PUBLIC_TEST, Split.PRIVATE_TEST),
            provider=ProviderConfig(policy="reference"),
        )
        result, paths = asyncio.run(p.evaluations.run(cfg, experiment_id=experiment_id))
        doc = {
            "experiment": "D_generalization",
            "evaluation_id": str(result.benchmark_id),
            "by_split": result.metrics["by_split"],
            "by_family": result.metrics["by_family"],
            "by_difficulty": result.metrics["by_difficulty"],
            "report": paths,
            "interpretation": "demo_provider is scripted; held-out results validate the protocol, not "
            "learned generalisation. Re-run with a model provider for real measurements.",
        }
        md = (
            f"# Experiment D — generalization\n\nEvaluation `{doc['evaluation_id']}`; full report: "
            f"`{paths['report_md']}`\n\n| Split | Verified success |\n|---|---|\n"
            + "".join(
                f"| {k} | {v['verified_success']['rate']:.1%} (n={v['n']}) |\n"
                for k, v in doc["by_split"].items()
            )
            + f"\n> {doc['interpretation']}\n"
        )
    else:  # C
        caps = detect()
        cfg = BenchmarkConfig(
            name="exp-C-baseline",
            splits=(Split.PUBLIC_TEST, Split.PRIVATE_TEST),
            provider=ProviderConfig(policy="reference"),
        )
        result, paths = asyncio.run(p.evaluations.run(cfg, experiment_id=experiment_id))
        doc = {
            "experiment": "C_base_vs_sft_vs_grpo",
            "baseline": {
                "provider": "demo_provider/reference (scripted)",
                "evaluation_id": str(result.benchmark_id),
                "verified_success": result.metrics["overall"]["verified_success"],
                "report": paths,
            },
            "sft": f"NOT EXECUTED: {caps.modes['sft'].reason}; no base model weights available locally",
            "grpo": f"NOT EXECUTED: {caps.modes['grpo'].reason}",
            "capabilities": caps.as_dict(),
        }
        md = (
            "# Experiment C — base vs SFT vs SFT+GRPO\n\n"
            f"- Baseline (scripted demo provider): {doc['baseline']['verified_success']['rate']:.1%} "
            f"(n={doc['baseline']['verified_success']['n']}) — report `{paths['report_md']}`\n"
            f"- SFT: **{doc['sft']}**\n- GRPO: **{doc['grpo']}**\n\n"
            "No trained-model comparison is reported because none was executed.\n"
        )
    files = _write(out_dir, f"experiment_{name}", doc, md)
    return {
        "experiment": name,
        "kind": kind,
        "experiment_id": str(experiment_id),
        "files": files,
        "headline": _headline(name, doc),
    }


def _headline(name: str, doc: dict[str, Any]) -> Any:
    if name == "B":
        return {
            d: {
                "fp_rate": v["false_positive_reward_rate"]["rate"],
                "tp_rate": v["true_positive_reward_rate"]["rate"],
            }
            for d, v in doc["designs"].items()
        }
    if name == "E":
        return {"cases": doc["n_cases"], "all_as_expected": doc["all_as_expected"]}
    if name == "A":
        return {
            k: {"selected": v["selected"], "misleading": v["misleading_claimed_success"]}
            for k, v in doc["levels"].items()
        }
    if name == "D":
        return {k: v["verified_success"]["rate"] for k, v in doc["by_split"].items()}
    return {
        "baseline": doc["baseline"]["verified_success"]["rate"],
        "sft": doc["sft"][:13],
        "grpo": doc["grpo"][:13],
    }
