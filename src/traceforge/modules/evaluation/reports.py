"""JSON + Markdown report generation. Reports only values present in the inputs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from traceforge import __version__
from traceforge.modules.evaluation.runner import BenchmarkResult

DEMO_DISCLAIMER = (
    "Runs used the deterministic scripted `demo_provider`. It is not a learned model; these numbers "
    "validate the pipeline (environment, verifier, reward, statistics), not model capability."
)


def _pct(p: dict[str, Any] | None) -> str:
    if not p:
        return "n/a"
    return f"{p['rate']:.1%} [{p['ci_low']:.1%}, {p['ci_high']:.1%}] (n={p['n']})"


def _fmt(x: Any, digits: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def benchmark_document(
    result: BenchmarkResult, artifacts: dict[str, Any] | None = None
) -> dict[str, Any]:
    cfg = result.config
    demo = cfg.provider.kind == "demo"
    return {
        "report_type": "benchmark",
        "traceforge_version": __version__,
        "benchmark_id": str(result.benchmark_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "config": cfg.model_dump(mode="json"),
        "config_hash": cfg.config_hash,
        "duration_seconds": result.duration_seconds,
        "cancelled": result.cancelled,
        "demo_provider": demo,
        "disclaimer": DEMO_DISCLAIMER if demo else None,
        "run_mode": cfg.run_mode,
        "official": cfg.official and result.leakage.ok,
        "evidence": result.metrics.get("evidence"),
        "leakage": result.leakage.as_dict(),
        "metrics": result.metrics,
        "artifacts": artifacts or {},
        "extra": result.extra,
    }


def benchmark_markdown(doc: dict[str, Any]) -> str:
    m = doc["metrics"]
    o = m["overall"]
    cfg = doc["config"]
    lines = [
        f"# TraceForge benchmark report — {cfg['name']}",
        "",
        f"- Benchmark id: `{doc['benchmark_id']}`",
        f"- Generated: {doc['generated_at']} (TraceForge {doc['traceforge_version']})",
        f"- Config hash: `{doc['config_hash']}`",
        f"- Provider: `{cfg['provider']['kind']}` / `{cfg['provider']['model_id']}`"
        + (f" (policy `{cfg['provider']['policy']}`)" if cfg["provider"]["kind"] == "demo" else ""),
        f"- Splits: {', '.join(cfg['splits'])}; reward policy `{cfg['reward_policy']}`; "
        f"verifier profile `{cfg['verifier_profile']}`; sandbox `{cfg['sandbox']}`",
        f"- Split policy: `{m.get('split_policy', {}).get('id')}` v{m.get('split_policy', {}).get('version')}",
        "",
    ]
    if not doc.get("official", True):
        lines += [
            "> **NON-OFFICIAL (diagnostic) RUN.** Leakage/lineage validation "
            + ("failed" if not doc["leakage"]["ok"] else "was not enforced")
            + "; these results must not be reported as benchmark results.",
            "",
        ]
    if doc.get("disclaimer"):
        lines += [f"> **Note:** {doc['disclaimer']}", ""]
    ev = doc.get("evidence") or {}
    if ev:
        lines += [
            f"- Evidence type: **{ev.get('kind')}** (learned-model evidence: "
            f"{ev.get('learned_model_evidence')})",
            f"- Independent tasks: **{ev.get('n_tasks')}**; episodes: {ev.get('n_episodes')}; "
            f"seeds: {ev.get('seeds')}",
        ]
        if ev.get("repeated_seed_note"):
            lines.append(f"- {ev['repeated_seed_note']}")
        lines.append("")
    lines += [
        "## Methodology",
        "",
        "Each task instance runs in a fresh sandbox. The agent acts only through allow-listed tools; every",
        "action is recorded. After the episode an independent verifier runs hidden security tests, hidden",
        "functional tests and the pristine visible tests against the candidate in a separate environment,",
        "plus state, safety and evidence checks. Reward is computed only from the verification result.",
        f"Proportions use Wilson score intervals at {cfg['confidence']:.0%} confidence; mean reward uses a",
        f"seeded percentile bootstrap ({cfg['bootstrap_samples']} resamples). Sample sizes are small;",
        "intervals are wide and no significance claims are made.",
        "",
        f"Statistical units: {m.get('statistical_units', 'episode-level Wilson intervals only')}",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Verified success | {_pct(o['verified_success'])} |",
        f"| Partial success | {_pct(o['partial_success'])} |",
        f"| False-positive reward rate | {_pct(o['false_positive_reward_rate'])} |",
        f"| Regression failure rate | {_pct(o['regression_failure_rate'])} |",
        f"| Verifier error rate | {_pct(o['verifier_error_rate'])} |",
        f"| Mean reward | {_fmt(o['mean_reward'])} (CI {_fmt((o['mean_reward_ci'] or [None])[0])}–"
        f"{_fmt((o['mean_reward_ci'] or [None, None])[1])}) |",
        f"| Tool calls / attempt | {_fmt(o['tool_calls_per_attempt'], 2)} |",
        f"| Tool calls / success | {_fmt(o['tool_calls_per_success'], 2)} |",
        f"| Action rejection rate | {_fmt(o['action_rejection_rate'])} |",
        f"| Mean task duration (s) | {_fmt(o['mean_duration_seconds'], 2)} |",
        f"| Environment compute (s) | {_fmt(o['environment_compute_seconds'], 2)} |",
        f"| Token usage / cost | {'not reported by provider' if o['token_usage'] is None else o['token_usage']} |",
        "",
    ]
    tc = o.get("task_clustered")
    if tc:
        lines += [
            "## Task-clustered uncertainty",
            "",
            f"Independent tasks n = {o.get('n_tasks')}; episodes n = {o.get('n_episodes')}.",
            "",
            "| Metric | Estimate | Task-clustered CI | Clusters |",
            "|---|---|---|---|",
        ]
        for key, label in (
            ("verified_success", "Verified success"),
            ("false_positive_reward_rate", "False-positive reward rate"),
            ("hidden_security_pass_rate", "Hidden security pass rate"),
            ("regression_failure_rate", "Regression failure rate"),
            ("mean_reward", "Mean reward"),
        ):
            c = tc.get(key) or {}
            ci = (
                "undefined (< 2 tasks)"
                if c.get("ci_low") is None
                else f"[{c['ci_low']:.3f}, {c['ci_high']:.3f}]"
            )
            lines.append(f"| {label} | {_fmt(c.get('estimate'))} | {ci} | {c.get('n_clusters')} |")
        lines.append("")
    sens = m.get("reward_threshold_sensitivity")
    if sens:
        lines += [
            "## Reward-threshold sensitivity",
            "",
            "| Threshold | Reward-positive rate | False-positive reward rate | FP (task-clustered CI) |",
            "|---|---|---|---|",
        ]
        for row in sens:
            c = row["false_positive_reward_rate_task_clustered"]
            ci = "n/a" if c.get("ci_low") is None else f"[{c['ci_low']:.3f}, {c['ci_high']:.3f}]"
            lines.append(
                f"| {row['threshold']} | {_pct(row['reward_positive_rate'])} | "
                f"{_pct(row['false_positive_reward_rate'])} | {ci} |"
            )
        lines.append("")
    for title, key in (
        ("By split", "by_split"),
        ("By family", "by_family"),
        ("By difficulty", "by_difficulty"),
    ):
        lines += [f"## {title}", "", "| Group | Verified success | Mean reward |", "|---|---|---|"]
        for group, g in m[key].items():
            lines.append(f"| {group} | {_pct(g['verified_success'])} | {_fmt(g['mean_reward'])} |")
        lines.append("")
    if m.get("task_level"):
        lines += [
            "## Task-level summary",
            "",
            "| Task | Family | Split | Episodes | Verified success | Mean reward | Consistent |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in m["task_level"]:
            lines.append(
                f"| {t['task_id']} | {t['family']} | {t['split']} | {t['n_episodes']} | "
                f"{t['verified_success_rate']:.0%} | {t['mean_reward']:.3f} | "
                f"{t['outcome_consistent_across_episodes']} |"
            )
        lines.append("")
    lines += [
        "## Per-task results",
        "",
        "| Task | Family | Split | Verdict | Reward | Calls | Reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in m["per_task"]:
        reasons = ", ".join(r["failure_reasons"]) or "—"
        lines.append(
            f"| {r['task_id']} | {r['family']} | {r['split']} | {r['verdict']} | "
            f"{r['reward']:.3f} | {r['tool_calls']} | {reasons} |"
        )
    leak = doc["leakage"]
    lines += [
        "",
        "## Split integrity",
        "",
        f"- Leakage check: {'PASS' if leak['ok'] else 'FAIL'}; run mode `{doc.get('run_mode', 'official')}`; "
        f"task counts by split: {json.dumps(leak['task_counts_by_split'])}",
        f"- Checks: {', '.join(leak.get('checks', []))}",
        f"- Training lineage sources: {', '.join(leak.get('training_sources') or []) or 'none declared'}"
        f" ({len(leak.get('training_task_ids') or [])} training tasks)",
    ]
    lines += [f"- {p}" for p in leak["problems"]]
    lines += [f"- training overlap: {p}" for p in leak.get("training_overlap") or []]
    lines += [
        "",
        "## Limitations",
        "",
        "- Synthetic, offline task families; small sample sizes.",
        "- Local sandbox provider is a process sandbox, not an isolation boundary.",
        "- Hidden tests can only show the presence of the tested properties, not total absence of bugs.",
    ]
    if doc.get("artifacts"):
        lines += ["", "## Artifacts", ""] + [f"- {k}: `{v}`" for k, v in doc["artifacts"].items()]
    return "\n".join(lines) + "\n"
