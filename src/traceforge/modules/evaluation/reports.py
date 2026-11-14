"""JSON + Markdown report generation. Reports only values present in the inputs.

Unknown or empty measurements are rendered as "n/a" or "not reported" with their sample sizes,
never as zero. Every metric line states its denominator; interval methods and the report tier
are stated in the methodology section (see docs/metrics-contract.md).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from traceforge import __version__
from traceforge.modules.evaluation.runner import BenchmarkResult

DEMO_DISCLAIMER = (
    "Runs used the deterministic scripted `demo_provider`. It is not a learned model; these numbers "
    "validate the pipeline (environment, verifier, reward, statistics), not model capability."
)
SMALL_SAMPLE = 10
ALL = "all scheduled episodes"


def _pct(p: dict[str, Any] | None) -> str:
    """Wilson proportion; an empty denominator is 'n/a (n=0)', never 0%."""
    if not p:
        return "n/a"
    if p.get("rate") is None:
        return f"n/a (n={p.get('n', 0)})"
    return f"{p['rate']:.1%} [{p['ci_low']:.1%}, {p['ci_high']:.1%}] (n={p['n']})"


def _fmt(x: Any, digits: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def _usd(x: Any) -> str:
    return "not reported" if x is None else f"${x:.6f}"


def _cov(c: dict[str, Any] | None) -> str:
    if not c:
        return "n/a"
    frac = c.get("fraction")
    return f"{c['covered']}/{c['n']}" + ("" if frac is None else f" ({frac:.0%})")


def _ci(c: dict[str, Any]) -> str:
    if c.get("ci_low") is None:
        return "undefined (< 2 tasks)"
    return f"[{c['ci_low']:.3f}, {c['ci_high']:.3f}]"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


FP_LABELS = {
    "independent_labels": "False-positive reward rate (independent labels)",
    "mixed": "False-positive reward rate (partly independent labels, rest verifier-derived)",
    "verifier_derived": "Reward-verifier disagreement (verifier-derived; not an independently "
    "labelled false-positive rate)",
}


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
        "metrics_schema_version": result.metrics.get("metrics_schema_version"),
        "report_tier": (result.metrics.get("report") or {}).get("tier"),
        "leakage": result.leakage.as_dict(),
        "metrics": result.metrics,
        "artifacts": artifacts or {},
        "extra": result.extra,
    }


def _header(doc: dict[str, Any], m: dict[str, Any], o: dict[str, Any]) -> list[str]:
    cfg = doc["config"]
    prov = cfg["provider"]
    report = m.get("report") or {}
    split = m.get("split_policy", {})
    schema = m.get("metrics_schema_version", o.get("metrics_schema_version", "1"))
    policy = f" (policy `{prov['policy']}`)" if prov["kind"] == "demo" else ""
    lines = [
        f"# TraceForge benchmark report — {cfg['name']}",
        "",
        f"- Benchmark id: `{doc['benchmark_id']}`",
        f"- Generated: {doc['generated_at']} (TraceForge {doc['traceforge_version']})",
        f"- Config hash: `{doc['config_hash']}`",
        f"- Provider: `{prov['kind']}` / `{prov['model_id']}`{policy}",
        f"- Splits: {', '.join(cfg['splits'])}; reward policy `{cfg['reward_policy']}`; "
        f"verifier profile `{cfg['verifier_profile']}`; sandbox `{cfg['sandbox']}`",
        f"- Split policy: `{split.get('id')}` v{split.get('version')}",
        f"- Metric schema: v{schema}; report tier: **{report.get('tier', 'development')}**",
        "",
    ]
    if not doc.get("official", True):
        why = "failed" if not doc["leakage"]["ok"] else "was not enforced"
        lines += [
            f"> **NON-OFFICIAL (diagnostic) RUN.** Leakage/lineage validation {why}; these "
            "results must not be reported as benchmark results.",
            "",
        ]
    if doc.get("disclaimer"):
        lines += [f"> **Note:** {doc['disclaimer']}", ""]
    if report.get("tier", "development") != "final":
        lines += [f"> **Development-grade intervals.** {report.get('note', '')}".rstrip(), ""]
    ev = doc.get("evidence") or {}
    if ev:
        lines += [
            f"- Evidence type: **{ev.get('kind')}** (learned-model evidence: "
            f"{ev.get('learned_model_evidence')})",
            f"- Independent tasks: **{ev.get('n_tasks')}**; episodes: {ev.get('n_episodes')}; "
            f"seeds: {ev.get('seeds')}; independently labelled episodes: {o.get('labelled_n', 0)}",
        ]
        if ev.get("repeated_seed_note"):
            lines.append(f"- {ev['repeated_seed_note']}")
        lines.append("")
    return lines


def _methodology(doc: dict[str, Any], m: dict[str, Any], o: dict[str, Any]) -> list[str]:
    cfg = doc["config"]
    stats = o.get("statistics") or {}
    units = m.get("statistical_units", "episode-level Wilson intervals only")
    return [
        "## Methodology",
        "",
        "Each task instance runs in a fresh sandbox. The agent acts only through allow-listed "
        "tools; every action is recorded. After the episode an independent verifier runs hidden "
        "security tests, hidden functional tests and the pristine visible tests against the "
        "candidate in a separate environment, plus state, safety and evidence checks. Reward is "
        "computed only from the verification result.",
        "",
        f"- Proportions: Wilson score intervals at {cfg['confidence']:.0%} confidence, "
        "episode-level and descriptive (episodes of one task are not independent).",
        f"- Task-clustered intervals: {stats.get('task_cluster_interval', 'n/a')}; estimand: "
        f"{stats.get('task_cluster_estimand', 'n/a')}. These are the intervals for claims "
        "about the task population (unit: independent task).",
        "- Mean reward interval: episode-level percentile bootstrap (descriptive). Bootstrap: "
        f"{cfg['bootstrap_samples']} resamples, seed {cfg['seed']}.",
        f"- Latency percentiles: {stats.get('percentile_method', 'n/a')}.",
        "- Missing telemetry is excluded and reported as coverage; unknown totals are `not "
        "reported`, never zero. Empty denominators are `n/a (n=0)`. Sample sizes are small; no "
        "significance claims are made.",
        "",
        f"Statistical units: {units}",
        "",
    ]


def _overall(o: dict[str, Any]) -> list[str]:
    pf = o.get("provider_failures") or {}
    source = o.get("false_positive_reward_label_source", "verifier_derived")
    ci = o["mean_reward_ci"] or [None, None]
    rows: list[tuple[str, ...]] = [
        ("Verified success (end-to-end)", _pct(o["verified_success"]), f"PASS / {ALL}"),
        ("Partial success", _pct(o["partial_success"]), f"PARTIAL / {ALL}"),
        ("Verifier error rate", _pct(o["verifier_error_rate"]), f"ERROR or INCONCLUSIVE / {ALL}"),
        ("Provider failure rate", _pct(pf.get("rate")), f"provider failures / {ALL}"),
        (
            "Reward-positive rate",
            _pct(o["reward_positive_rate"]),
            f"reward >= {o.get('reward_threshold')} / {ALL}",
        ),
        (
            FP_LABELS.get(source, FP_LABELS["verifier_derived"]),
            _pct(o["false_positive_reward_rate"]),
            f"rewarded and not truly successful / {ALL}",
        ),
        (
            "Hidden security pass rate",
            _pct(o["hidden_security_pass_rate"]),
            "PASS / episodes where the check executed",
        ),
        ("Regression failure rate", _pct(o["regression_failure_rate"]), f"FAIL / {ALL}"),
        (
            "Regression failure rate (executed)",
            _pct(o.get("regression_failure_rate_executed")),
            "FAIL / episodes where the check executed",
        ),
        (
            "Mean reward",
            f"{_fmt(o['mean_reward'])} (CI {_fmt(ci[0])}–{_fmt(ci[1])})",
            f"sum of rewards / {ALL}",
        ),
        ("Tool calls / attempt", _fmt(o["tool_calls_per_attempt"], 2), f"tool calls / {ALL}"),
        (
            "Tool calls / success",
            _fmt(o["tool_calls_per_success"], 2),
            "tool calls of PASS episodes / PASS episodes",
        ),
        ("Action rejection rate", _fmt(o["action_rejection_rate"]), "rejected / tool calls"),
        ("Mean task duration (s)", _fmt(o["mean_duration_seconds"], 2), f"seconds / {ALL}"),
        (
            "Environment compute (s)",
            _fmt(o["environment_compute_seconds"], 2),
            "sum of verification check seconds",
        ),
    ]
    if o.get("reward_precision") is not None or o.get("reward_recall") is not None:
        rows += [
            (
                "Reward precision (labelled)",
                _pct(o.get("reward_precision")),
                "rewarded and truly successful / rewarded labelled episodes",
            ),
            (
                "Reward recall (labelled)",
                _pct(o.get("reward_recall")),
                "truly successful and rewarded / truly successful labelled episodes",
            ),
        ]
    return ["## Overall", "", *_table(("Metric", "Value", "Numerator / denominator"), rows), ""]


def _provider_section(o: dict[str, Any]) -> list[str]:
    pf = o.get("provider_failures") or {}
    vo = o.get("verifier_outcomes_without_provider_failure") or {}
    by_kind = json.dumps(pf.get("by_kind") or {}, sort_keys=True)
    by_source = json.dumps(pf.get("by_source") or {}, sort_keys=True)
    return [
        "## Provider failures and verifier outcomes",
        "",
        "Provider failures (timeouts, rate limits, HTTP/transport errors, malformed responses) are "
        "model-access or infrastructure outcomes. They count as non-successes in end-to-end "
        "verified success and are reported separately from verifier errors.",
        "",
        f"- Provider failure rate: {_pct(pf.get('rate'))}; timeouts: {_pct(pf.get('timeout_rate'))}",
        f"- By kind: {by_kind}; by source: {by_source}",
        f"- Among the {vo.get('n', 0)} episodes without a provider failure (conditional, "
        f"descriptive): verified success {_pct(vo.get('verified_success'))}; verifier errors "
        f"{_pct(vo.get('verifier_error_rate'))}",
        "",
    ]


def _cost_section(o: dict[str, Any]) -> list[str]:
    cost = o.get("cost") or {}
    tok = o.get("tokens") or {}
    cov = o.get("telemetry_coverage") or {}
    rows: list[tuple[str, ...]] = [
        (
            "Total cost (USD)",
            _usd(cost.get("total_usd")),
            "sum of episode costs (null if any is unknown)",
        ),
        ("Cost / attempt (USD)", _usd(cost.get("per_attempt_usd")), f"total cost / {ALL}"),
        (
            "Cost / verified success (USD)",
            _usd(cost.get("per_verified_success_usd")),
            "total cost / PASS episodes",
        ),
    ]
    if cost.get("lower_bound_usd") is not None:
        rows.append(
            (
                "**Lower bound** on total cost (USD)",
                _usd(cost["lower_bound_usd"]),
                f"sum of the {cost.get('episodes_with_known_cost')} known episode costs; "
                "the remaining costs are unknown",
            )
        )
    rows += [
        ("Cost coverage", _cov(cov.get("cost")), "episodes with a known cost / all episodes"),
        (
            "Prompt / completion tokens",
            f"{_fmt(tok.get('prompt_total'))} / {_fmt(tok.get('completion_total'))}",
            "summed (null unless usage is complete)",
        ),
        ("Tokens / attempt", _fmt(tok.get("per_attempt"), 1), f"total tokens / {ALL}"),
        (
            "Tokens / verified success",
            _fmt(tok.get("per_verified_success"), 1),
            "total tokens / PASS episodes",
        ),
        (
            "Verified successes / 1K tokens *(secondary)*",
            _fmt(tok.get("verified_successes_per_1000_tokens"), 4),
            "PASS x 1000 / total tokens",
        ),
        (
            "Usage coverage",
            _cov(cov.get("usage")),
            "episodes whose every request reported usage / all episodes",
        ),
    ]
    return [
        "## Cost and tokens",
        "",
        *_table(("Metric", "Value", "Numerator / denominator"), rows),
        "",
    ]


def _latency_section(o: dict[str, Any]) -> list[str]:
    lat = o.get("latency") or {}
    rows: list[tuple[str, ...]] = []
    for key, label in (
        ("request_seconds", "Generation request (s, incl. retries)"),
        ("episode_seconds", "Episode wall time (s)"),
        ("generation_seconds_per_episode", "Generation time per episode (s)"),
    ):
        p = lat.get(key) or {}
        n = p.get("n", 0)
        flag = " *(small sample)*" if 0 < n < SMALL_SAMPLE else ""
        rows.append(
            (
                label + flag,
                str(n),
                *(_fmt(p.get(q)) for q in ("p50", "p90", "p95", "max")),
            )
        )
    fr = o.get("finish_reasons") or {}
    cov = o.get("telemetry_coverage") or {}
    return [
        "## Latency",
        "",
        *_table(("Measure", "n", "p50", "p90", "p95", "max"), rows),
        "",
        f"Finish reasons (successful requests): {json.dumps(fr.get('counts') or {}, sort_keys=True)}; "
        f"truncated (`length`): {fr.get('truncated_length', 0)}",
        "",
        "## Telemetry coverage",
        "",
        *_table(
            ("Measurement", "Covered episodes"),
            [(k.replace("_", " "), _cov(v)) for k, v in cov.items()],
        ),
        "",
    ]


def _clustered_section(o: dict[str, Any]) -> list[str]:
    tc = o.get("task_clustered")
    if not tc:
        return []
    rows: list[tuple[str, ...]] = []
    for key, label in (
        ("verified_success", "Verified success"),
        ("false_positive_reward_rate", "False-positive reward / disagreement rate"),
        ("hidden_security_pass_rate", "Hidden security pass rate"),
        ("regression_failure_rate", "Regression failure rate"),
        ("provider_failure_rate", "Provider failure rate"),
        ("mean_reward", "Mean reward"),
    ):
        c = tc.get(key) or {}
        if c:
            rows.append((label, _fmt(c.get("estimate")), _ci(c), str(c.get("n_clusters"))))
    return [
        "## Task-clustered uncertainty",
        "",
        f"Independent tasks n = {o.get('n_tasks')}; episodes n = {o.get('n_episodes')}. "
        "Pooled-ratio estimand (tasks weighted by their episode count).",
        "",
        *_table(("Metric", "Estimate", "Task-clustered CI", "Clusters"), rows),
        "",
    ]


def _breakdowns(m: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    sens = m.get("reward_threshold_sensitivity")
    if sens:
        rows: list[tuple[str, ...]] = []
        for row in sens:
            c = row["false_positive_reward_rate_task_clustered"]
            rows.append(
                (
                    str(row["threshold"]),
                    _pct(row["reward_positive_rate"]),
                    _pct(row["false_positive_reward_rate"]),
                    "n/a" if c.get("ci_low") is None else _ci(c),
                )
            )
        header: tuple[str, ...] = (
            "Threshold",
            "Reward-positive rate",
            "False-positive / disagreement rate",
            "Task-clustered CI",
        )
        lines += ["## Reward-threshold sensitivity", "", *_table(header, rows), ""]
    for title, key in (
        ("By split", "by_split"),
        ("By family", "by_family"),
        ("By difficulty", "by_difficulty"),
    ):
        group_rows = [
            (str(group), _pct(g["verified_success"]), _fmt(g["mean_reward"]))
            for group, g in m[key].items()
        ]
        lines += [
            f"## {title}",
            "",
            *_table(("Group", "Verified success", "Mean reward"), group_rows),
            "",
        ]
    if m.get("task_level"):
        task_rows = [
            (
                str(t["task_id"]),
                str(t["family"]),
                str(t["split"]),
                str(t["n_episodes"]),
                f"{t['verified_success_rate']:.0%}",
                f"{t['mean_reward']:.3f}",
                str(t["outcome_consistent_across_episodes"]),
                str(t.get("provider_failures", 0)),
            )
            for t in m["task_level"]
        ]
        header = (
            "Task",
            "Family",
            "Split",
            "Episodes",
            "Verified success",
            "Mean reward",
            "Consistent",
            "Provider failures",
        )
        lines += ["## Task-level summary", "", *_table(header, task_rows), ""]
    episode_rows = [
        (
            str(r["task_id"]),
            str(r.get("seed")),
            str(r["family"]),
            str(r["split"]),
            str(r["verdict"]),
            f"{r['reward']:.3f}",
            str(r["tool_calls"]),
            str(r.get("termination_reason", "—")),
            ", ".join(r["failure_reasons"]) or "—",
        )
        for r in m["per_task"]
    ]
    header = (
        "Task",
        "Seed",
        "Family",
        "Split",
        "Verdict",
        "Reward",
        "Calls",
        "Termination",
        "Reasons",
    )
    lines += ["## Per-episode results", "", *_table(header, episode_rows)]
    return lines


def _integrity(doc: dict[str, Any]) -> list[str]:
    leak = doc["leakage"]
    sources = ", ".join(leak.get("training_sources") or []) or "none declared"
    lines = [
        "",
        "## Split integrity",
        "",
        f"- Leakage check: {'PASS' if leak['ok'] else 'FAIL'}; run mode "
        f"`{doc.get('run_mode', 'official')}`; task counts by split: "
        f"{json.dumps(leak['task_counts_by_split'])}",
        f"- Checks: {', '.join(leak.get('checks', []))}",
        f"- Training lineage sources: {sources} "
        f"({len(leak.get('training_task_ids') or [])} training tasks)",
    ]
    lines += [f"- {p}" for p in leak["problems"]]
    lines += [f"- training overlap: {p}" for p in leak.get("training_overlap") or []]
    lines += [
        "",
        "## Limitations",
        "",
        "- Synthetic, offline task families; small sample sizes.",
        "- Local sandbox provider is a process sandbox, not an isolation boundary.",
        "- Hidden tests can only show the presence of the tested properties, not total absence of "
        "bugs.",
        "- Unless labelled otherwise, false-positive reward is measured against the verifier "
        "itself (reward-verifier disagreement), not against independent ground truth.",
    ]
    if doc.get("artifacts"):
        lines += ["", "## Artifacts", ""] + [f"- {k}: `{v}`" for k, v in doc["artifacts"].items()]
    return lines


def benchmark_markdown(doc: dict[str, Any]) -> str:
    m = doc["metrics"]
    o = m["overall"]
    lines = [
        *_header(doc, m, o),
        *_methodology(doc, m, o),
        *_overall(o),
        *_provider_section(o),
        *_cost_section(o),
        *_latency_section(o),
        *_clustered_section(o),
        *_breakdowns(m),
        *_integrity(doc),
    ]
    return "\n".join(lines) + "\n"
