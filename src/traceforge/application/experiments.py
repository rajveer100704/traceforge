"""Experiment orchestration (A–H). Writes JSON + Markdown + a reproducibility bundle.

Unavailable parts are marked NOT EXECUTED with the reason; nothing is filled in.

A trajectory quality · B reward hacking · C base vs SFT vs GRPO (baseline only) ·
D generalisation · E verifier robustness · F curation ablation · G SFT vs GRPO (arms) ·
H unseen-attack robustness.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from traceforge.contracts.common import CapabilityUnavailable, sha256_json
from traceforge.contracts.experiments import (
    BenchmarkConfig,
    ExperimentSpec,
    ProviderConfig,
    TrainingLineage,
)
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
    "F": "curation_ablation",
    "G": "sft_vs_grpo_arms",
    "H": "unseen_attacks",
}
DEFAULT_SEEDS = (0, 1, 2)
CONFIG_DIR = Path(__file__).resolve().parents[3] / "configs"
SCRIPTED_NOTE = (
    "SCRIPTED-AGENT PIPELINE VALIDATION: demo_provider policies are deterministic scripts, not a "
    "learned model."
)


def _write(out_dir: Path, stem: str, doc: dict[str, Any], md: str) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(
        json.dumps(doc, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8", newline="\n")
    return {"json": str(out_dir / f"{stem}.json"), "markdown": str(out_dir / f"{stem}.md")}


def _load_config(name: str, path: Path | None) -> dict[str, Any]:
    path = path or CONFIG_DIR / f"experiment_{name}.yaml"
    if not path.is_file():
        return {}
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _benchmark_episodes(p: Platform, runs: list[Any]) -> list[dict[str, Any]]:
    out = []
    for r in runs:
        out.append(
            {
                "seed": r.metadata.get("seed"),
                "summary": r.model_dump(mode="json"),
                "episode": p.rollouts.load_episode(r.rollout_id).model_dump(mode="json"),
            }
        )
    return out


def run_experiment(
    p: Platform,
    name: str,
    out_dir: Path | None = None,
    *,
    seeds: tuple[int, ...] | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    if name not in NAMES:
        raise ValueError(f"unknown experiment {name}; choose from {sorted(NAMES)}")
    kind = NAMES[name]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_dir or p.settings.reports_dir / "experiments" / f"{name}_{kind}_{stamp}"
    seeds = tuple(seeds) if seeds else DEFAULT_SEEDS
    experiment_id = p.experiments.create(ExperimentSpec(name=f"experiment-{name}", kind=kind))
    bundle_kw: dict[str, Any] = {"seeds": list(seeds)}
    seed_arg = ",".join(str(s) for s in seeds)
    bundle_kw["commands"] = [f"uv run traceforge experiment run {name} --seeds {seed_arg}"]
    episodes: list[dict[str, Any]] = []
    if name == "B":
        doc = asyncio.run(
            ex.run_reward_hacking(
                p.executor, p.registry, per_template=1, seeds=seeds, sink=episodes
            )
        )
        md = ex.hacking_markdown(doc)
        bundle_kw["configs"] = {"experiment_B": {"per_template": 1, "seeds": list(seeds)}}
    elif name == "H":
        doc = asyncio.run(
            ex.run_unseen_attacks(
                p.executor, p.registry, per_template=1, seeds=seeds, sink=episodes
            )
        )
        md = ex.unseen_attacks_markdown(doc)
        bundle_kw["configs"] = {"experiment_H": {"per_template": 1, "seeds": list(seeds)}}
    elif name == "E":
        doc = ex.run_verifier_robustness(p.sandbox, p.registry, p.policies)
        md = ex.robustness_markdown(doc)
        bundle_kw["seeds"] = []
        bundle_kw["commands"] = [f"uv run traceforge experiment run {name}"]
        bundle_kw["episodes_reason"] = (
            "Experiment E verifies constructed in-memory episode states; every case is in "
            "metrics.json (cases)"
        )
    elif name == "A":
        doc, md = _experiment_a(p, episodes)
        bundle_kw["seeds"] = [0]
        bundle_kw["commands"] = [f"uv run traceforge experiment run {name}"]
        bundle_kw["datasets"] = [
            {"level": k, "dataset_version": v["dataset_version"]} for k, v in doc["levels"].items()
        ]
    elif name == "D":
        doc, md = _experiment_d(p, experiment_id, seeds, episodes, bundle_kw)
    elif name == "C":
        doc, md = _experiment_c(p, experiment_id, seeds, episodes, bundle_kw)
    elif name == "F":
        doc, md = _experiment_f(p, seeds, _load_config("F", config_path), episodes, bundle_kw)
    else:  # G
        doc, md = _experiment_g(
            p, experiment_id, _load_config("G", config_path), episodes, bundle_kw
        )
    files = _write(out_dir, f"experiment_{name}", doc, md)
    from traceforge.application.reproducibility import write_bundle

    if not episodes and "episodes_reason" not in bundle_kw:
        bundle_kw["episodes_reason"] = "no rollouts were executed by this experiment"
    bundle = write_bundle(p, out_dir, experiment=name, document=doc, episodes=episodes, **bundle_kw)
    return {
        "experiment": name,
        "kind": kind,
        "experiment_id": str(experiment_id),
        "files": files,
        "bundle": bundle["dir"],
        "headline": _headline(name, doc),
    }


# ----------------------------------------------------------------------------- A, C, D


def _experiment_a(p: Platform, episodes: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    policies = (
        "reference",
        "noop",
        "false_claim",
        "visible_overfit",
        "break_functionality",
        "malformed_then_reference",
    )
    trajectories = asyncio.run(
        ex.collect_verified_corpus(p.executor, p.registry, policies, per_template=1, sink=episodes)
    )
    caps = detect()
    sft = (
        "NOT EXECUTED: downstream SFT comparison across D1–D4 requires a real base model; see "
        f"Experiment F (sft mode: {caps.modes['sft'].reason})"
    )
    task_split = {b.spec.task_id: b.oracle.split.value for b in p.registry.list()}
    doc = ex.run_trajectory_quality(trajectories, task_split, sft, _imported_examples())
    doc["evidence"] = ex.SCRIPTED_EVIDENCE
    return doc, ex.trajectory_quality_markdown(doc)


def _imported_examples() -> list[Any]:
    from traceforge.application.demo import EXAMPLE_TRACES
    from traceforge.modules.trace_processing.ingestion import IngestionPipeline, split_jsonl
    from traceforge.modules.trace_processing.quality import QualityEngine

    parsed = IngestionPipeline(QualityEngine()).run(
        EXAMPLE_TRACES.name, split_jsonl(EXAMPLE_TRACES.read_bytes())
    )
    return [t for t, _a, _r in parsed.trajectories]


def _run_benchmark(
    p: Platform,
    cfg: BenchmarkConfig,
    experiment_id: Any,
    episodes: list[dict[str, Any]],
    bundle_kw: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    result, paths = asyncio.run(p.evaluations.run(cfg, experiment_id=experiment_id))
    episodes.extend(_benchmark_episodes(p, result.runs))
    bundle_kw.setdefault("configs", {})[cfg.name] = cfg.model_dump(mode="json")
    bundle_kw.setdefault("evaluation_ids", []).append(str(result.benchmark_id))
    return result, paths


def _experiment_d(
    p: Platform,
    experiment_id: Any,
    seeds: tuple[int, ...],
    episodes: list[dict[str, Any]],
    bundle_kw: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    cfg = BenchmarkConfig(
        name="exp-D-generalization",
        splits=(Split.VALIDATION, Split.PUBLIC_TEST, Split.PRIVATE_TEST),
        provider=ProviderConfig(policy="reference"),
        seeds=seeds,
    )
    result, paths = _run_benchmark(p, cfg, experiment_id, episodes, bundle_kw)
    m = result.metrics
    doc = {
        "experiment": "D_generalization",
        "evidence": m["evidence"],
        "evaluation_id": str(result.benchmark_id),
        "seeds": list(seeds),
        "by_split": m["by_split"],
        "by_family": m["by_family"],
        "by_difficulty": m["by_difficulty"],
        "task_level": m["task_level"],
        "leakage": result.leakage.as_dict(),
        "report": paths,
        "interpretation": "demo_provider is scripted; held-out results validate the protocol, not "
        "learned generalisation. Re-run with a model provider for real measurements.",
    }
    rows = "".join(
        f"| {k} | {v['verified_success']['rate']:.1%} | {v['n_tasks']} | {v['n_episodes']} | "
        f"{ex._cci(v['task_clustered']['verified_success'])} |\n"
        for k, v in doc["by_split"].items()
    )
    md = (
        f"# Experiment D — generalization\n\n> **{SCRIPTED_NOTE}**\n\n"
        f"Evaluation `{doc['evaluation_id']}`; seeds {list(seeds)}; full report: "
        f"`{paths['report_md']}`\n\n"
        "| Split | Verified success | Independent tasks | Episodes | Task-clustered CI |\n"
        "|---|---|---|---|---|\n" + rows + f"\n> {doc['interpretation']}\n"
    )
    return doc, md


def _experiment_c(
    p: Platform,
    experiment_id: Any,
    seeds: tuple[int, ...],
    episodes: list[dict[str, Any]],
    bundle_kw: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    caps = detect()
    cfg = BenchmarkConfig(
        name="exp-C-baseline",
        splits=(Split.PUBLIC_TEST, Split.PRIVATE_TEST),
        provider=ProviderConfig(policy="reference"),
        seeds=seeds,
    )
    result, paths = _run_benchmark(p, cfg, experiment_id, episodes, bundle_kw)
    o = result.metrics["overall"]
    doc = {
        "experiment": "C_base_vs_sft_vs_grpo",
        "evidence": result.metrics["evidence"],
        "baseline": {
            "provider": "demo_provider/reference (scripted)",
            "evaluation_id": str(result.benchmark_id),
            "verified_success": o["verified_success"],
            "verified_success_task_clustered": o["task_clustered"]["verified_success"],
            "n_tasks": o["n_tasks"],
            "n_episodes": o["n_episodes"],
            "report": paths,
        },
        "sft": f"NOT EXECUTED: {caps.modes['sft'].reason}; no base model weights available locally",
        "grpo": f"NOT EXECUTED: {caps.modes['grpo'].reason}",
        "capabilities": caps.as_dict(),
        "see_also": "Experiment G runs this comparison with model-provider arms when configured.",
    }
    md = (
        "# Experiment C — base vs SFT vs SFT+GRPO\n\n"
        f"> **{SCRIPTED_NOTE}**\n\n"
        f"- Baseline (scripted demo provider): {doc['baseline']['verified_success']['rate']:.1%} "
        f"({o['n_tasks']} independent tasks, {o['n_episodes']} episodes, seeds {list(seeds)}) — "
        f"report `{paths['report_md']}`\n"
        f"- SFT: **{doc['sft']}**\n- GRPO: **{doc['grpo']}**\n\n"
        "No trained-model comparison is reported because none was executed. See Experiment G.\n"
    )
    return doc, md


# ----------------------------------------------------------------------------- F


def _experiment_f(
    p: Platform,
    seeds: tuple[int, ...],
    cfg: dict[str, Any],
    episodes: list[dict[str, Any]],
    bundle_kw: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    from traceforge.contracts.datasets import CurationLevel
    from traceforge.modules.evaluation.contamination import check_training_rows
    from traceforge.modules.trace_processing.dataset_builder import DatasetBuilder, read_partition
    from traceforge.modules.trace_processing.quality import QualityEngine
    from traceforge.modules.trace_processing.validators import default_validators
    from traceforge.modules.training.sft.trainer import SFTConfig, run_sft

    training_cfg = dict(cfg.get("training") or {"mode": "cpu-test"})
    seeds = tuple(cfg.get("seeds") or seeds)
    eval_cfg = dict(cfg.get("evaluation") or {})
    providers: dict[str, Any] = dict(eval_cfg.get("providers") or {})
    policies = (
        "reference",
        "noop",
        "false_claim",
        "visible_overfit",
        "break_functionality",
        "malformed_then_reference",
    )
    trajectories = asyncio.run(
        ex.collect_verified_corpus(p.executor, p.registry, policies, per_template=1, sink=episodes)
    )
    known = {t.verification_id for t in trajectories if t.verification_id is not None}
    quality = QualityEngine(default_validators(None, known.__contains__))
    corpus = trajectories + _imported_examples()
    pairs = [(t, quality.assess(t)) for t in corpus]
    task_split = {b.spec.task_id: b.oracle.split.value for b in p.registry.list()}
    controlled = {k: v for k, v in training_cfg.items() if k not in ("seed", "output_dir")}
    control_hash = sha256_json(controlled)
    is_pretrained = training_cfg.get("mode") == "sft"
    arms: dict[str, Any] = {}
    datasets = []
    for level in CurationLevel:
        built = DatasetBuilder().build(
            f"expF-{level.value}", pairs, level=level, task_split=task_split.get
        )
        parts = {
            f.split: read_partition(built.partitions[f.name]) for f in built.manifest.partitions
        }
        train, val = parts.get("train", []), parts.get("validation", [])
        leaks = check_training_rows([*train, *val], p.registry)
        datasets.append(
            {
                "level": level.value,
                "dataset_version": built.manifest.version,
                "content_hash": built.manifest.content_hash,
                "rows_by_split": {k: len(v) for k, v in parts.items()},
            }
        )
        arm: dict[str, Any] = {
            "dataset": datasets[-1],
            "leakage_problems": leaks,
            "training": [],
            "control_hash": control_hash,
        }
        for seed in seeds:
            if leaks:
                arm["training"].append({"seed": seed, "status": "REFUSED", "reason": leaks[:5]})
                continue
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    sft_cfg = SFTConfig.model_validate(
                        {**training_cfg, "seed": seed, "output_dir": str(Path(tmp) / "out")}
                    )
                    meta = run_sft(sft_cfg, train, val)
                arm["training"].append(
                    {
                        "seed": seed,
                        "status": "EXECUTED",
                        "mode": meta["mode"],
                        "base_model": meta["base_model"],
                        "base_model_revision": meta["base_model_revision"],
                        "config_hash": meta["config_hash"],
                        "steps": meta["step"],
                        "train_examples": meta["train_examples"],
                        "validation_examples": meta["validation_examples"],
                        "dropped_no_target": meta["dropped_no_target"],
                        "first_loss": meta["first_loss"],
                        "last_loss": meta["last_loss"],
                        "validation_loss": meta["validation_loss"],
                        "seconds": meta["seconds"],
                        "checkpoint_sha256": meta["checkpoint_sha256"],
                        "device": meta["device"],
                    }
                )
            except CapabilityUnavailable as exc:
                arm["training"].append(
                    {"seed": seed, "status": "NOT EXECUTED", "reason": exc.message}
                )
        provider = providers.get(level.value)
        if provider and is_pretrained:
            arm["evaluation"] = _evaluate_arm(
                p,
                level.value,
                provider,
                eval_cfg,
                sorted({str(r["task_id"]) for r in train + val if r.get("task_id")}),
                episodes,
                bundle_kw,
            )
        else:
            arm["evaluation"] = {
                "status": "NOT EXECUTED",
                "reason": (
                    "training used cpu-test (tiny random model, not pretrained); evaluating it "
                    "would measure nothing"
                    if not is_pretrained
                    else f"no inference endpoint configured for arm {level.value}"
                ),
            }
        arms[level.value] = arm
    doc = {
        "experiment": "F_curation_ablation",
        "evidence": {
            "kind": "pipeline_validation" if not is_pretrained else "model_training",
            "learned_model_evidence": False,
            "label": (
                "CONTROLLED-TRAINING PIPELINE CHECK ONLY — cpu-test trains a tiny randomly "
                "initialised model; losses are not evidence about curation quality."
                if not is_pretrained
                else "Pretrained-model training; see per-arm evaluation status."
            ),
        },
        "controlled": controlled,
        "control_hash": control_hash,
        "seeds": list(seeds),
        "evaluation_splits": eval_cfg.get("splits", ["private_test"]),
        "arms": arms,
        "model_improvement_claim": "NONE — no pretrained model was trained and evaluated"
        if not any(a["evaluation"].get("status") == "EXECUTED" for a in arms.values())
        else "see arms[*].evaluation",
    }
    bundle_kw["configs"] = {"experiment_F": cfg or {"training": training_cfg}}
    bundle_kw["datasets"] = datasets
    bundle_kw["seeds"] = list(seeds)
    bundle_kw["models"] = [
        {
            "model_id": training_cfg.get("base_model"),
            "revision": training_cfg.get("base_model_revision"),
            "tokenizer": "byte-level CharTokenizer (cpu-test)"
            if not is_pretrained
            else f"{training_cfg.get('base_model')}@{training_cfg.get('base_model_revision')}",
            "pretrained": is_pretrained,
        }
    ]
    bundle_kw["checkpoints"] = [
        {"arm": k, "seed": t["seed"], "sha256": t["checkpoint_sha256"]}
        for k, a in arms.items()
        for t in a["training"]
        if t.get("status") == "EXECUTED"
    ]
    return doc, _f_markdown(doc)


def _evaluate_arm(
    p: Platform,
    arm: str,
    provider: dict[str, Any],
    eval_cfg: dict[str, Any],
    training_task_ids: list[str],
    episodes: list[dict[str, Any]],
    bundle_kw: dict[str, Any],
) -> dict[str, Any]:
    cfg = BenchmarkConfig(
        name=f"exp-{arm}",
        splits=tuple(Split(s) for s in eval_cfg.get("splits", ["private_test"])),
        seeds=tuple(eval_cfg.get("seeds", [0, 1, 2])),
        provider=ProviderConfig.model_validate(provider),
        sandbox=str(eval_cfg.get("sandbox", "local")),
        training_lineage=TrainingLineage(task_ids=tuple(training_task_ids)),
    )
    try:
        result, paths = _run_benchmark(p, cfg, None, episodes, bundle_kw)
    except Exception as exc:  # recorded, never converted into a result
        return {"status": "FAILED", "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return {
        "status": "EXECUTED",
        "evaluation_id": str(result.benchmark_id),
        "report": paths,
        "metrics": _arm_metrics(result.metrics),
    }


def _f_markdown(doc: dict[str, Any]) -> str:
    lines = [
        "# Experiment F — curation ablation (D1–D4)",
        "",
        f"> **{doc['evidence']['label']}**",
        "",
        f"Model-improvement claim: **{doc['model_improvement_claim']}**.",
        "",
        f"Controlled across arms (hash `{doc['control_hash'][:12]}`): "
        f"`{json.dumps(doc['controlled'], sort_keys=True)}`; seeds {doc['seeds']}; evaluation "
        f"splits {doc['evaluation_splits']}.",
        "",
        "| Arm | Dataset version | Train / val rows | Training runs | Mean last loss | Evaluation |",
        "|---|---|---|---|---|---|",
    ]
    for name, a in doc["arms"].items():
        runs = a["training"]
        done = [t for t in runs if t["status"] == "EXECUTED"]
        loss = (
            f"{sum(t['last_loss'] for t in done) / len(done):.3f}"
            if done and all(t["last_loss"] is not None for t in done)
            else "n/a"
        )
        status = ", ".join(sorted({t["status"] for t in runs}))
        ev = a["evaluation"]
        rows = a["dataset"]["rows_by_split"]
        lines.append(
            f"| {name} | `{a['dataset']['dataset_version']}` | {rows.get('train', 0)} / "
            f"{rows.get('validation', 0)} | {len(done)}/{len(runs)} {status} | {loss} | "
            f"{ev['status']}: {ev.get('reason', ev.get('evaluation_id', ''))} |"
        )
    lines += [
        "",
        "Losses from cpu-test runs are reported only to show that every arm trained under the same",
        "controls; they are not comparable evidence of dataset quality (random tiny model, byte",
        "tokenizer, a handful of steps).",
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- G


def _arm_metrics(m: dict[str, Any]) -> dict[str, Any]:
    o = m["overall"]
    tc = o["task_clustered"]
    return {
        "n_tasks": o["n_tasks"],
        "n_episodes": o["n_episodes"],
        "verified_success": o["verified_success"],
        "verified_success_task_clustered": tc["verified_success"],
        "hidden_security_pass_rate": o["hidden_security_pass_rate"],
        "hidden_security_pass_rate_task_clustered": tc["hidden_security_pass_rate"],
        "regression_failure_rate": o["regression_failure_rate"],
        "regression_failure_rate_task_clustered": tc["regression_failure_rate"],
        "false_positive_reward_rate": o["false_positive_reward_rate"],
        "false_positive_reward_rate_task_clustered": tc["false_positive_reward_rate"],
        "tool_calls_per_verified_success": o["tool_calls_per_success"],
        "mean_episode_seconds": o["mean_duration_seconds"],
        "mean_generation_seconds": o["mean_generation_seconds"],
        "token_usage": o["token_usage"],
        "by_family": {k: v["verified_success"] for k, v in m["by_family"].items()},
        "by_difficulty": {k: v["verified_success"] for k, v in m["by_difficulty"].items()},
        "by_seed": {k: v["verified_success"]["rate"] for k, v in m["by_seed"].items()},
        "evidence": m["evidence"],
    }


def _training_cost(p: Platform, run_ids: list[str]) -> dict[str, Any]:
    import uuid as _uuid

    from traceforge.infrastructure.database import models as dbm

    runs: list[dict[str, Any]] = []
    for rid in run_ids:
        with p.db.session() as s:
            row = s.get(dbm.TrainingRun, _uuid.UUID(rid))
            if row is None:
                runs.append({"training_run_id": rid, "status": "NOT FOUND"})
                continue
            metrics = dict(row.metrics or {})
            runs.append(
                {
                    "training_run_id": rid,
                    "mode": row.mode,
                    "status": row.status,
                    "base_model": row.base_model,
                    "base_model_revision": row.base_model_revision,
                    "seconds": metrics.get("seconds"),
                    "device": metrics.get("device"),
                }
            )
    secs = [float(r["seconds"]) for r in runs if r.get("seconds") is not None]
    return {"runs": runs, "total_training_seconds": sum(secs) if secs else None}


def _experiment_g(
    p: Platform,
    experiment_id: Any,
    cfg: dict[str, Any],
    episodes: list[dict[str, Any]],
    bundle_kw: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    bench = dict(cfg.get("benchmark") or {})
    pricing = dict(cfg.get("pricing") or {})
    arms_cfg: dict[str, Any] = dict(
        cfg.get("arms") or {"pipeline_check": {"provider": {"kind": "demo", "policy": "reference"}}}
    )
    seeds = tuple(bench.get("seeds") or DEFAULT_SEEDS)
    arms: dict[str, Any] = {}
    for arm, spec in arms_cfg.items():
        spec = dict(spec or {})
        provider = spec.get("provider")
        run_ids = [str(r) for r in spec.get("training_run_ids") or []]
        if not provider:
            arms[arm] = {
                "status": "NOT EXECUTED",
                "reason": "no provider configured (needs an inference endpoint for this model)",
                "training": _training_cost(p, run_ids) if run_ids else None,
            }
            continue
        bc = BenchmarkConfig(
            name=f"exp-G-{arm}",
            splits=tuple(Split(s) for s in bench.get("splits", ["public_test", "private_test"])),
            seeds=seeds,
            provider=ProviderConfig.model_validate(provider),
            reward_policy=str(bench.get("reward_policy", "verified-v1")),
            verifier_profile=str(bench.get("verifier_profile", "v3_full")),
            sandbox=str(bench.get("sandbox", "local")),
            training_lineage=TrainingLineage(training_run_ids=tuple(run_ids)),
        )
        try:
            result, paths = _run_benchmark(p, bc, experiment_id, episodes, bundle_kw)
        except Exception as exc:
            arms[arm] = {"status": "FAILED", "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}
            continue
        metrics = _arm_metrics(result.metrics)
        tokens = metrics["token_usage"]
        cost = None
        if tokens and pricing.get("prompt_per_1k") is not None:
            cost = tokens["prompt_tokens"] / 1000 * float(pricing["prompt_per_1k"]) + tokens[
                "completion_tokens"
            ] / 1000 * float(pricing.get("completion_per_1k") or 0)
        arms[arm] = {
            "status": "EXECUTED",
            "learned_model": bc.provider.kind != "demo",
            "evaluation_id": str(result.benchmark_id),
            "report": paths,
            "metrics": metrics,
            "inference_cost_usd": cost,
            "training": _training_cost(p, run_ids) if run_ids else None,
        }
    learned = [a for a in arms.values() if a.get("status") == "EXECUTED" and a.get("learned_model")]
    doc = {
        "experiment": "G_sft_vs_grpo",
        "benchmark": {**bench, "seeds": list(seeds)},
        "arms": arms,
        "comparison": "NOT AVAILABLE — fewer than two learned-model arms executed"
        if len(learned) < 2
        else "see arms",
        "evidence": {
            "learned_model_evidence": bool(learned),
            "label": "Only arms with learned_model=true are model evidence; pipeline_check is a "
            "scripted reference policy.",
        },
    }
    bundle_kw["configs"] = {**bundle_kw.get("configs", {}), "experiment_G": cfg}
    bundle_kw["seeds"] = list(seeds)
    return doc, _g_markdown(doc)


def _g_markdown(doc: dict[str, Any]) -> str:
    def pct(x: dict[str, Any] | None) -> str:
        return "n/a" if not x else f"{x['rate']:.1%} [{x['ci_low']:.1%}, {x['ci_high']:.1%}]"

    lines = [
        "# Experiment G — base vs SFT vs SFT+GRPO",
        "",
        f"> **{doc['evidence']['label']}**",
        "",
        f"Comparison: **{doc['comparison']}**. Identical benchmark for every arm: "
        f"`{json.dumps(doc['benchmark'], sort_keys=True)}`.",
        "",
        "| Arm | Status | Tasks / episodes | Verified success | Task-clustered CI | Hidden security pass "
        "| Regression failure | FP reward rate | Calls / success | Gen. latency (s) | Tokens |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, a in doc["arms"].items():
        if a["status"] != "EXECUTED":
            lines.append(f"| {name} | {a['status']}: {a['reason']} |" + " |" * 9)
            continue
        m = a["metrics"]
        tokens = m["token_usage"]["total_tokens"] if m["token_usage"] else "not reported"
        lines.append(
            f"| {name}{'' if a['learned_model'] else ' (scripted)'} | EXECUTED | "
            f"{m['n_tasks']} / {m['n_episodes']} | {pct(m['verified_success'])} | "
            f"{ex._cci(m['verified_success_task_clustered'])} | "
            f"{pct(m['hidden_security_pass_rate'])} | {pct(m['regression_failure_rate'])} | "
            f"{pct(m['false_positive_reward_rate'])} | {m['tool_calls_per_verified_success']} | "
            f"{m['mean_generation_seconds']} | {tokens} |"
        )
    lines += ["", "Per-family/difficulty results and training cost are in the JSON document.", ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- headline


def _headline(name: str, doc: dict[str, Any]) -> Any:
    if name == "B":
        return {
            d: {
                "fp_rate": v["false_positive_reward_rate"]["rate"],
                "tp_rate": v["true_positive_reward_rate"]["rate"],
            }
            for d, v in doc["designs"].items()
        }
    if name == "H":
        return {
            s: {d: v["false_positive_reward_rate"]["rate"] for d, v in st["designs"].items()}
            for s, st in doc["sets"].items()
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
    if name == "F":
        return {
            k: {
                "training": sorted({t["status"] for t in a["training"]}),
                "evaluation": a["evaluation"]["status"],
            }
            for k, a in doc["arms"].items()
        }
    if name == "G":
        return {k: a["status"] for k, a in doc["arms"].items()}
    return {
        "baseline": doc["baseline"]["verified_success"]["rate"],
        "sft": doc["sft"][:13],
        "grpo": doc["grpo"][:13],
    }
