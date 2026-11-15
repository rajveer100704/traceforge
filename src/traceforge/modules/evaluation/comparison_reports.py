"""JSON document and Markdown report of a paired benchmark comparison.

Both views are rendered from one finite document (``finite_document``): a non-finite number is
``null`` in JSON and "n/a" in Markdown, never "nan".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from traceforge import __version__
from traceforge.modules.evaluation import comparison_contract as cc
from traceforge.modules.evaluation.reports import _fmt, _num, _table
from traceforge.modules.evaluation.serialization import finite_document

ORDER = tuple(cc.RECORD_SCHEMAS["comparison_document"])


def comparison_document(core: dict[str, Any]) -> dict[str, Any]:
    """Finite comparison document with identity fields, in contract field order."""
    doc = {
        **core,
        "report_type": "benchmark_comparison",
        "comparison_id": str(uuid.uuid4()),
        "generated_at": datetime.now(UTC).isoformat(),
        "traceforge_version": __version__,
    }
    ordered = {k: doc[k] for k in ORDER if k in doc}
    return finite_document(ordered)


def refusal_document(refusal: dict[str, Any]) -> dict[str, Any]:
    return finite_document(
        {
            "report_type": "benchmark_comparison_refusal",
            "comparison_schema_version": cc.COMPARISON_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "traceforge_version": __version__,
            **refusal,
        }
    )


def _ci(d: dict[str, Any]) -> str:
    if d.get("ci_low") is None or d.get("ci_high") is None:
        return f"undefined ({d.get('note') or 'n/a'})"
    return f"[{_num(d['ci_low'], '+.3f')}, {_num(d['ci_high'], '+.3f')}]"


def _metric_rows(group: dict[str, Any], keys: list[str] | None = None) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for key, m in group["metrics"].items():
        if keys is not None and key not in keys:
            continue
        d = m["difference"]
        rows.append(
            (
                f"`{key}`" + (" *(B)*" if m["class"] == "B" else ""),
                m["direction"],
                _fmt(m["baseline"]["estimate"], 4),
                _fmt(m["candidate"]["estimate"], 4),
                _num(d["estimate"], "+.4f"),
                _ci(d),
                str(d["n_clusters"]),
                _ci(m["instance_sensitivity"]),
                f"{m['coverage']['pairs_used']}/{m['coverage']['pairs_total']}",
                m["interpretation"]
                + (f" ({m['not_comparable_reason']})" if m["not_comparable_reason"] else ""),
            )
        )
    return rows


HEADER = (
    "Metric",
    "Better",
    "Baseline",
    "Candidate",
    "Difference (C - B)",
    "Scenario-clustered CI",
    "Scenarios",
    "Instance-clustered CI (sensitivity)",
    "Pairs used",
    "Interpretation",
)


def _counts_line(c: dict[str, Any]) -> str:
    return (
        f"{c['n_structural_scenarios']} structural scenarios ({c['n_task_instances']} task "
        f"instances, {c['n_pairs']} episode pairs, {c['n_families']} families)"
    )


def comparison_markdown(doc: dict[str, Any]) -> str:
    b, c, mt = doc["baseline"], doc["candidate"], doc["matching"]
    groups = doc["groups"]
    lines = [
        f"# TraceForge paired comparison: {b['name']} vs {c['name']}",
        "",
        f"> **{doc['evidence_banner']}**",
        ">",
        f"> Report tier: **{doc['report_tier']}** · Evidence tier: **{doc['evidence_tier']}** · "
        f"Evidence source: **{doc['evidence_source']}** · Learned-model evidence: "
        f"**{doc['learned_model_evidence']}**",
        "",
        f"- Comparison id: `{doc['comparison_id']}` · fingerprint `{doc['comparison_fingerprint']}`",
        f"- Generated: {doc['generated_at']} (TraceForge {doc['traceforge_version']})",
        "",
        "## Runs and seeds",
        "",
        *_table(
            ("", "Baseline", "Candidate"),
            [
                ("Evaluation", f"`{b['evaluation_id']}`", f"`{c['evaluation_id']}`"),
                ("Source", b["source"], c["source"]),
                (
                    "Provider",
                    f"{b['provider_kind']} {b['model_id'] or b['policy'] or ''}",
                    f"{c['provider_kind']} {c['model_id'] or c['policy'] or ''}",
                ),
                (
                    "Run mode / official",
                    f"{b['run_mode']} / {b['official']}",
                    f"{c['run_mode']} / {c['official']}",
                ),
                ("Status", b["status"], c["status"]),
                (
                    "Verifier",
                    f"{b['verifier_profile']} {b['verifier_version']}",
                    f"{c['verifier_profile']} {c['verifier_version']}",
                ),
                ("Episodes", str(b["n_episodes"]), str(c["n_episodes"])),
            ],
        ),
        "",
        f"- Episode seeds: {doc['seeds']['episode_seeds']} · provider.seed: baseline "
        f"{doc['seeds']['provider_seed']['baseline']}, candidate "
        f"{doc['seeds']['provider_seed']['candidate']} (matched: "
        f"{doc['seeds']['provider_seed']['matched']}) · bootstrap seed "
        f"{doc['seeds']['bootstrap_seed']}",
        f"- {doc['seeds']['provider_seed_note'].capitalize()}.",
        "",
        "## Matching and coverage",
        "",
        f"**{_counts_line(mt)}.** Status: **{mt['status']}** (coverage policy "
        f"{mt['coverage_policy']}; official: {mt['official']}).",
        "",
    ]
    if mt["problems"]:
        lines += _table(
            ("Problem", "Severity", "Detail"),
            [(p["code"], p["severity"], p["detail"]) for p in mt["problems"]],
        )
        lines.append("")
    if mt["excluded"]:
        lines.append(f"Excluded observations (diagnostic): {len(mt['excluded'])}.")
        lines.append("")
    diffs = [d for d in mt["config_differences"]]
    if diffs:
        lines += _table(
            ("Config difference", "Baseline", "Candidate", "Allowed"),
            [
                (d["path"], str(d["baseline"]), str(d["candidate"]), str(d["allowed"]))
                for d in diffs
            ],
        )
        lines.append("")
    pe = doc["primary_endpoint"]
    lines += ["## Primary endpoint", ""]
    if pe["status"] == "available":
        primary = groups["by_split"][cc.PRIMARY_SPLIT]
        lines += [
            f"`{pe['group']}` / `{pe['metric']}` — {_counts_line(primary['counts'])}; scenario "
            f"tier **{primary['scenario_tier']}**.",
            "",
            *_table(HEADER, _metric_rows(primary, [cc.PRIMARY_METRIC])),
            "",
            "### Secondary metrics on the primary endpoint",
            "",
            *_table(
                HEADER,
                _metric_rows(primary, [k for k in primary["metrics"] if k != cc.PRIMARY_METRIC]),
            ),
            "",
        ]
    else:
        lines += [f"**Unavailable:** {pe['reason']}. Pooled results are not substituted.", ""]
    lines += ["## Secondary groups", ""]
    sections = [("Overall (pooled)", groups["overall"])]
    sections += [
        (f"Split `{k}`", g) for k, g in groups["by_split"].items() if k != cc.PRIMARY_SPLIT
    ]
    sections += [(f"Family `{k}`", g) for k, g in groups["by_family"].items()]
    sections += [(f"Difficulty `{k}`", g) for k, g in groups["by_difficulty"].items()]
    for title, g in sections:
        lines += [
            f"### {title}",
            "",
            f"{_counts_line(g['counts'])}; tier {g['scenario_tier']}.",
            "",
        ]
        if g["in_distribution_note"]:
            lines += [f"> Note: {g['in_distribution_note']}.", ""]
        lines += [*_table(HEADER, _metric_rows(g)), ""]
    lines += [
        "## Per structural scenario",
        "",
        *_table(
            (
                "Scenario",
                "Family",
                "Templates",
                "Instances",
                "Pairs",
                "Verified success B → C",
                "Change",
            ),
            [
                (
                    f"`{s['scenario_id']}`",
                    s["family"],
                    ", ".join(s["templates"]),
                    str(len(s["task_instances"])),
                    str(s["n_pairs"]),
                    f"{_fmt(s['metrics']['verified_success']['baseline_mean'], 3)} → "
                    f"{_fmt(s['metrics']['verified_success']['candidate_mean'], 3)}",
                    s["change"],
                )
                for s in doc["per_scenario"]
            ],
        ),
        "",
        "## Per task instance",
        "",
        *_table(
            ("Task", "Scenario", "Split", "Seeds", "Verdicts B->C", "Change"),
            [
                (
                    f"{t['task_id']}@{t['task_version']}",
                    t["scenario_id"],
                    t["split"],
                    ",".join(str(s) for s in t["seeds"]),
                    ", ".join(f"{k}: {v}" for k, v in t["verdict_transitions"].items()),
                    t["change"],
                )
                for t in doc["per_task"]
            ],
        ),
        "",
        "## Cost, tokens and latency",
        "",
    ]
    for name in ("cost_coverage", "usage_coverage"):
        cv = doc[name]
        lines.append(
            f"- {name.replace('_', ' ').capitalize()}: baseline {cv['baseline']['status']} "
            f"({cv['baseline']['covered']}/{cv['baseline']['n']}), candidate "
            f"{cv['candidate']['status']} ({cv['candidate']['covered']}/{cv['candidate']['n']}); "
            + ("comparable." if cv["comparable"] else f"not comparable: {cv['reason']}.")
        )
    lines.append("")
    lines += _table(
        (
            "Latency (descriptive)",
            "Baseline p50/p90/p95",
            "Candidate p50/p90/p95",
            "Difference p50/p90/p95",
        ),
        [
            (
                k,
                " / ".join(_fmt(v["baseline"][q], 3) for q in ("p50", "p90", "p95")),
                " / ".join(_fmt(v["candidate"][q], 3) for q in ("p50", "p90", "p95")),
                " / ".join(_fmt(v["difference"][q], 3) for q in ("p50", "p90", "p95")),
            )
            for k, v in doc["latency"].items()
        ],
    )
    lines += ["", "## Warnings", ""]
    lines += [f"- `{w['code']}`: {w['detail']}" for w in doc["warnings"]] or ["- none"]
    st = doc["statistics"]
    lines += [
        "",
        "## Limitations",
        "",
        *[f"- {x}" for x in doc["limitations"]],
        "",
        "## Methods",
        "",
        f"- Primary cluster unit: **{st['primary_cluster_unit']}**; secondary (sensitivity, "
        f"never interpreted): {st['secondary_cluster_unit']}.",
        f"- {st['method']}; {st['samples']} resamples; seed {st['bootstrap_seed']}; confidence "
        f"{_num(st['confidence'], '.0%')}.",
        *[f"- Estimand ({k}): {v}." for k, v in st["estimands"].items()],
        f"- {st['eligibility_note']}",
        "",
        "## Reproducibility",
        "",
        f"- Task versions: {doc['protocol']['task_versions']}; scenario source "
        f"{doc['protocol']['scenario_source']} (registry v{doc['protocol']['scenario_registry_version']}, "
        f"oracle normalisation v{doc['protocol']['oracle_normalisation_version']}).",
        f"- Comparison fingerprint: `{doc['comparison_fingerprint']}`.",
    ]
    if doc.get("non_finite_fields"):
        lines.append(f"- Non-finite values written as null: {doc['non_finite_fields']}.")
    return "\n".join(lines).rstrip("\n") + "\n"
