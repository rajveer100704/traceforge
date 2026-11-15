"""Versioned contract of paired benchmark comparisons (v0.3 PR 2).

``docs/comparison-contract.md`` is generated from this module (``render_markdown``); tests fail
if it drifts, if an emitted field is undocumented, or if a Class A metric is not a registered
PR 1B metric.

Metric classes:

* **A**: the per-arm estimand is exactly a PR 1B ``summarize`` metric; the comparison estimate
  is the difference of the two arms' PR 1B values (with complete coverage).
* **B**: comparison-only estimands with their own population; always secondary/diagnostic and
  never given a directional label.
* **C**: descriptive (latency percentiles): per-arm values and a point difference, no interval.
"""

from __future__ import annotations

from dataclasses import dataclass

from traceforge.modules.evaluation.scenarios import LEGACY_SCENARIOS, SCENARIO_REGISTRY_VERSION

COMPARISON_SCHEMA_VERSION = "1"
INSUFFICIENT_BELOW = 10
EXPLORATORY_BELOW = 20
PRIMARY_GROUP = "by_split.private_test"
PRIMARY_SPLIT = "private_test"
PRIMARY_METRIC = "verified_success"
ELIGIBILITY_NOTE = (
    "20 structural scenarios is an eligibility gate for a directional statement, not proof of "
    "statistical validity, power or generalisation. Fewer than 10 scenarios: "
    "insufficient_scenarios; 10-19: exploratory_only."
)
IN_DISTRIBUTION_NOTE = (
    "legacy public_test scenarios also appear in train; this measures in-distribution "
    "performance, not held-out structural generalisation"
)
PROVIDER_SEED_NOTE = (
    "paired episode seeds do not guarantee equivalent provider-generation randomness; "
    "providers may ignore requested seeds"
)
LIMITATIONS: tuple[str, ...] = (
    "Structural scenarios are assumed independent; they share task families, authors, the "
    "sandbox harness and the verifier.",
    "Episode seeds are crossed with tasks (seed s is used for every task); seed-level effects "
    "are not modelled.",
    PROVIDER_SEED_NOTE[0].upper() + PROVIDER_SEED_NOTE[1:] + ".",
    "In the legacy benchmark, public_test scenarios also appear in train: public results "
    "measure in-distribution performance; only private_test holds out structure.",
    "Pooled estimands weight scenarios by their number of episode pairs.",
    "PR 1B per-arm task_clustered intervals cluster by task instance and are not shown here.",
    "paired_conditional metrics select on both arms' outcomes and may be biased; diagnostic.",
    "The benchmark is synthetic and offline: results do not establish real-world security "
    "performance.",
)

SCENARIO_TIERS = ("insufficient_scenarios", "exploratory_only", "directional_eligible")
EVIDENCE_TIERS = (
    "diagnostic",
    "no_primary_endpoint",
    "insufficient_scenarios",
    "exploratory_only",
    "directional_eligible",
)
INTERPRETATIONS = (
    "not_comparable",
    "undefined",
    "diagnostic_only",
    "secondary",
    "insufficient_scenarios",
    "exploratory_only",
    "improvement",
    "regression",
    "no_detectable_difference",
)

# problem code -> (severity, meaning). hard: refused in every mode; strict: refused under strict
# coverage, allowed with a warning under intersection; not_official: never refused, but the
# comparison cannot be official.
PROBLEM_CODES: dict[str, tuple[str, str]] = {
    "invalid_record": ("hard", "a record is malformed or lacks an episode seed"),
    "duplicate_observation": ("hard", "the same (task_id, task_version, seed) occurs twice"),
    "task_version_mismatch": ("hard", "one task_id appears with different task versions"),
    "unknown_task": ("hard", "task_id@task_version is unknown to the task registry"),
    "task_identity_mismatch": ("hard", "record metadata or environment disagrees with registry"),
    "scenario_unresolved": ("hard", "no authoritative scenario for this task"),
    "scenario_unverifiable": ("hard", "task version has no authoritative scenario source"),
    "oracle_identity_mismatch": ("hard", "recomputed oracle fingerprint differs from the pin"),
    "scenario_mismatch": ("hard", "the arms or the metadata disagree on a task's scenario"),
    "scenario_identity_collision": ("hard", "distinct scenarios share one oracle fingerprint"),
    "verifier_mismatch": ("hard", "verifier profile or version differs"),
    "benchmark_identity_mismatch": ("hard", "benchmark identity differs between arms"),
    "never_variable_config": ("hard", "a NEVER_VARIABLE configuration field differs"),
    "empty_run": ("hard", "a run has no episodes"),
    "invalid_bundle": ("hard", "bundle integrity, config or evaluation id cannot be verified"),
    "unknown_evaluation": ("hard", "the evaluation run does not exist"),
    "coverage_mismatch": ("strict", "episode keys differ between arms"),
    "reward_policy_mismatch": ("strict", "reward policy hash differs"),
    "provider_seed_mismatch": ("strict", "provider.seed differs"),
    "config_mismatch": ("strict", "a must-match configuration field differs"),
    "final_tier_requirement": ("hard", "a final report requirement is not met"),
    "non_authoritative": ("not_official", "a record is not v3_full/authoritative"),
    "run_not_completed": ("not_official", "a run did not complete"),
    "run_not_official": ("not_official", "a run is diagnostic or failed its leakage checks"),
    "bundle_source": ("not_official", "bundle-loaded runs are always diagnostic"),
}


@dataclass(frozen=True)
class ComparisonMetricSpec:
    key: str
    cls: str  # A | B | C
    kind: str  # mean | ratio | descriptive
    direction: str  # higher | lower | neutral
    category: str
    population: str
    numerator: str
    denominator: str
    comparable_when: str
    pr1b_key: str | None = None  # Class A: the PR 1B metric whose difference this is


ALL = "matched episode pairs with a known value in both arms"
_A = "A"


def _a(
    key: str,
    kind: str,
    direction: str,
    category: str,
    num: str,
    den: str,
    when: str,
    pr1b: str | None = None,
) -> ComparisonMetricSpec:
    return ComparisonMetricSpec(
        key, _A, kind, direction, category, ALL, num, den, when, pr1b or key
    )


COMPARABLE_METRICS: tuple[ComparisonMetricSpec, ...] = (
    _a("verified_success", "mean", "higher", "outcome", "PASS verdicts", "pairs", "always"),
    _a("partial_success", "mean", "neutral", "outcome", "PARTIAL verdicts", "pairs", "always"),
    _a(
        "verifier_error_rate",
        "mean",
        "lower",
        "reliability",
        "ERROR/INCONCLUSIVE",
        "pairs",
        "always",
    ),
    _a(
        "reward_positive_rate",
        "mean",
        "neutral",
        "reward",
        "reward >= 0.5",
        "pairs",
        "reward policy hashes equal",
    ),
    _a(
        "false_positive_reward_rate",
        "mean",
        "lower",
        "reward",
        "rewarded and not an authoritative PASS (reward-verifier disagreement)",
        "pairs",
        "reward policy hashes equal",
    ),
    _a("regression_failure_rate", "mean", "lower", "safety", "regression FAIL", "pairs", "always"),
    _a(
        "regression_failure_rate_executed",
        "ratio",
        "lower",
        "safety",
        "regression FAIL",
        "regression PASS or FAIL",
        "always",
    ),
    _a(
        "hidden_security_pass_rate",
        "ratio",
        "higher",
        "safety",
        "hidden-security PASS",
        "executed hidden-security checks",
        "always",
    ),
    _a(
        "provider_failures.rate",
        "mean",
        "lower",
        "reliability",
        "provider failures",
        "pairs",
        "always",
    ),
    _a(
        "mean_reward",
        "mean",
        "higher",
        "reward",
        "final reward",
        "pairs",
        "reward policy hashes equal",
    ),
    _a("tool_calls_per_attempt", "mean", "lower", "efficiency", "tool calls", "pairs", "always"),
    _a(
        "tool_calls_per_success",
        "ratio",
        "lower",
        "efficiency",
        "tool calls of PASS episodes",
        "PASS episodes",
        "each arm has >= 1 PASS",
    ),
    _a(
        "mean_duration_seconds",
        "mean",
        "lower",
        "efficiency",
        "episode wall time",
        "pairs",
        "always",
    ),
    _a(
        "cost.per_attempt_usd",
        "mean",
        "lower",
        "cost",
        "episode cost",
        "pairs",
        "cost complete in both arms",
    ),
    _a(
        "cost.per_verified_success_usd",
        "ratio",
        "lower",
        "cost",
        "episode cost",
        "PASS episodes",
        "cost complete in both arms and >= 1 PASS each",
    ),
    _a(
        "tokens.per_attempt",
        "mean",
        "lower",
        "cost",
        "prompt + completion tokens",
        "pairs",
        "usage complete in both arms",
    ),
    _a(
        "tokens.per_verified_success",
        "ratio",
        "lower",
        "cost",
        "prompt + completion tokens",
        "PASS episodes",
        "usage complete in both arms and >= 1 PASS each",
    ),
    ComparisonMetricSpec(
        "paired_conditional.verified_success",
        "B",
        "mean",
        "higher",
        "outcome",
        "pairs where neither arm had a provider failure",
        "PASS verdicts",
        "those pairs",
        ">= 1 such pair",
    ),
    ComparisonMetricSpec(
        "provider_retries_per_episode",
        "B",
        "mean",
        "lower",
        "reliability",
        "matched pairs",
        "provider retries",
        "pairs",
        "attempt counts known for every pair in both arms",
    ),
)
METRIC_SPECS: dict[str, ComparisonMetricSpec] = {s.key: s for s in COMPARABLE_METRICS}
DESCRIPTIVE_LATENCY = ("request_seconds", "episode_seconds")

# Record schemas: every emitted record has exactly these fields (optional ones may be absent).
RECORD_SCHEMAS: dict[str, dict[str, str]] = {
    "comparison_document": {
        "report_type": "Always 'benchmark_comparison'.",
        "comparison_schema_version": "Comparison contract version.",
        "comparison_id": "Random id of this comparison (uuid4).",
        "comparison_fingerprint": "sha256 of the request and both arms' record digests.",
        "generated_at": "UTC timestamp.",
        "traceforge_version": "TraceForge version.",
        "request": "The comparison request.",
        "report_tier": "development | final (report rigour).",
        "evidence_tier": "What claims the comparison may support (see Evidence tiers).",
        "evidence_banner": "Human-readable statement of the evidence tier.",
        "evidence_source": "model_comparison | pipeline_validation.",
        "learned_model_evidence": "False whenever a scripted provider is involved.",
        "seeds": "Episode seeds, provider seeds and the bootstrap seed (record seeds).",
        "baseline": "Baseline run (record run).",
        "candidate": "Candidate run (record run).",
        "matching": "Matching result (record matching).",
        "protocol": "Shared protocol identity (record protocol).",
        "primary_endpoint": "Primary endpoint (record primary_endpoint).",
        "groups": "overall, by_split, by_family, by_difficulty (record group).",
        "per_scenario": "One row per structural scenario (record per_scenario).",
        "per_task": "One row per task instance (record per_task).",
        "pairs": "Optional (include_pairs): one row per episode pair.",
        "latency": "Descriptive Class C latency comparison.",
        "cost_coverage": "Per-arm cost coverage and comparability.",
        "usage_coverage": "Per-arm token usage coverage and comparability.",
        "statistics": "Methods, estimands, thresholds.",
        "report": "Report tier block.",
        "warnings": "List of {code, detail}.",
        "limitations": "Statements that apply to every comparison.",
        "non_finite_fields": "Optional: paths of non-finite values written as null.",
    },
    "seeds": {
        "episode_seeds": "Benchmark episode seeds (pair-key component).",
        "provider_seed": "{baseline, candidate, matched, sent_to_provider}: configured "
        "BenchmarkConfig.provider.seed (must match for official comparisons) and which seed the "
        "runner actually sends to the provider.",
        "bootstrap_seed": "Statistical resampling seed (independent of both runs).",
        "provider_seed_note": "Providers may ignore requested seeds.",
    },
    "run": {
        "evaluation_id": "Evaluation run id.",
        "source": "evaluation | bundle.",
        "bundle_dir": "Bundle directory (bundle source) or null.",
        "config_name": "Bundle config name or null.",
        "name": "Benchmark config name.",
        "config_hash": "BenchmarkConfig hash (not used for matching: it includes the name).",
        "run_mode": "official | diagnostic.",
        "official": "Run official and leakage checks passed.",
        "status": "Run status.",
        "provider_kind": "Provider kind.",
        "model_id": "Model id.",
        "policy": "Scripted policy or null.",
        "verifier_profile": "Verifier profile of the records.",
        "verifier_version": "Verifier version of the records.",
        "reward_policy": "{id, version, hash} of the records.",
        "n_episodes": "Episodes loaded.",
    },
    "matching": {
        "status": "matched | diagnostic | refused.",
        "official": "True only for a strictly matched comparison with no not_official problem.",
        "coverage_policy": "strict | intersection.",
        "problems": "List of {code, severity, detail, keys}.",
        "excluded": "List of {key, side, reason} (intersection only).",
        "config_differences": "List of {path, baseline, candidate, allowed}.",
        "n_pairs": "Matched episode pairs.",
        "n_task_instances": "Distinct task_id@task_version.",
        "n_structural_scenarios": "Distinct structural scenarios (independent units).",
        "n_families": "Distinct task families.",
    },
    "protocol": {
        "task_versions": "Task versions compared.",
        "scenario_source": "legacy_registry | catalogue.",
        "scenario_registry_version": "Scenario registry version.",
        "oracle_normalisation_version": "Oracle fingerprint normalisation version.",
        "benchmark_identity": "Benchmark identity (catalogue runs) or null (legacy).",
        "split_policy": "Task split policy.",
        "verifier": "{profile, version, authoritative}.",
        "reward_policy": "{id, version, hash}.",
        "reward_threshold": "Reward-positive threshold.",
    },
    "primary_endpoint": {
        "status": "available | unavailable.",
        "group": "by_split.private_test or null (never substituted).",
        "metric": "verified_success.",
        "reason": "Why unavailable, else null.",
    },
    "group": {
        "role": "primary | secondary.",
        "in_distribution_note": "public_test overlap note, else null.",
        "counts": "{n_pairs, n_task_instances, n_structural_scenarios, n_families, "
        "instances_per_scenario, seeds_per_instance, scenario_weights}.",
        "scenario_tier": "insufficient_scenarios | exploratory_only | directional_eligible.",
        "metrics": "metric key -> record metric_comparison.",
    },
    "metric_comparison": {
        "metric": "Metric key.",
        "class": "A | B (see Metrics).",
        "kind": "mean | ratio.",
        "direction": "higher | lower | neutral (which way is favourable).",
        "category": "outcome | safety | reward | reliability | efficiency | cost.",
        "population": "Pairs the metric is computed over.",
        "baseline": "Baseline arm estimate (record arm_estimate).",
        "candidate": "Candidate arm estimate (record arm_estimate).",
        "difference": "Primary scenario-clustered difference (record difference).",
        "instance_sensitivity": "Secondary task-instance-clustered interval (record "
        "sensitivity); never interpreted.",
        "relative_difference": "difference / baseline (non-negative metrics, baseline > 0).",
        "coverage": "{pairs_used, pairs_total}.",
        "scenario_tier": "From this metric's n_clusters.",
        "interpretation": "See Interpretation.",
        "estimate_direction": "favourable | unfavourable | none (sign only, never a claim).",
        "not_comparable_reason": "Why not comparable, else null.",
    },
    "difference": {
        "estimate": "Candidate minus baseline.",
        "ci_low": "Percentile interval low (null if undefined).",
        "ci_high": "Percentile interval high (null if undefined).",
        "confidence": "Confidence level.",
        "samples": "Bootstrap resamples.",
        "seed": "Bootstrap seed.",
        "method": "Bootstrap method.",
        "cluster_unit": "structural_scenario (primary) | task_instance (sensitivity).",
        "n_clusters": "Clusters with data for this metric.",
        "n_observations": "Pairs used (mean metrics) or null (ratio metrics).",
        "undefined_replicates": "Ratio replicates with a zero denominator (0 for mean metrics).",
        "note": "Why the interval is undefined, else null.",
    },
    "sensitivity": {
        "estimate": "Same estimate as the primary difference.",
        "ci_low": "Instance-clustered interval low.",
        "ci_high": "Instance-clustered interval high.",
        "confidence": "Confidence level.",
        "samples": "Bootstrap resamples.",
        "seed": "Bootstrap seed.",
        "method": "Bootstrap method.",
        "cluster_unit": "task_instance.",
        "n_clusters": "Task instances with data.",
        "n_observations": "Pairs used (mean metrics) or null.",
        "undefined_replicates": "Ratio replicates with a zero denominator.",
        "note": "Why the interval is undefined, else null.",
        "width_ratio": "Instance-clustered width / scenario-clustered width.",
        "label": "Why this interval is never interpreted.",
    },
    "arm_estimate": {
        "estimate": "numerator / denominator, or null with an empty denominator.",
        "numerator": "Sum of per-episode numerators over used pairs.",
        "denominator": "Pairs used (mean metrics) or summed denominators (ratio metrics).",
        "n": "Denominator as an integer.",
    },
    "latency_entry": {
        "baseline": "{n, p50, p90, p95, max} (PR 1B percentiles).",
        "candidate": "{n, p50, p90, p95, max}.",
        "difference": "{p50, p90, p95}: candidate minus baseline; descriptive, no interval.",
    },
    "coverage_block": {
        "baseline": "Record coverage_arm.",
        "candidate": "Record coverage_arm.",
        "comparable": "True only when both arms are complete.",
        "reason": "Why not comparable, else null.",
    },
    "coverage_arm": {
        "status": "complete | partial | unknown | overflow | no_episodes.",
        "covered": "Episodes with known cost / complete usage.",
        "n": "Episodes.",
        "total": "Total (null unless complete).",
        "lower_bound": "Cost lower bound (partial coverage only; never differenced).",
    },
    "statistics": {
        "primary_cluster_unit": "structural_scenario.",
        "secondary_cluster_unit": "task_instance.",
        "estimands": "Mean, ratio and paired-conditional estimands.",
        "method": "Bootstrap methods.",
        "samples": "Bootstrap resamples.",
        "bootstrap_seed": "Bootstrap seed.",
        "confidence": "Confidence level.",
        "thresholds": "{insufficient_below, exploratory_below}.",
        "eligibility_note": "What the 20-scenario gate does and does not mean.",
    },
    "report": {
        "tier": "development | final.",
        "bootstrap_samples": "Resamples used.",
        "final_min_bootstrap_samples": "10,000.",
        "final_min_primary_scenarios": "10.",
        "note": "Final-tier requirements.",
    },
    "pair": {
        "key": "[task_id, task_version, episode_seed].",
        "scenario_id": "Structural scenario.",
        "family": "Task family.",
        "template": "Template.",
        "split": "Split.",
        "difficulty": "Difficulty.",
        "baseline_rollout_id": "Baseline rollout.",
        "candidate_rollout_id": "Candidate rollout.",
        "values": "Mean-metric values {baseline, candidate}.",
    },
    "refusal_document": {
        "report_type": "Always 'benchmark_comparison_refusal'.",
        "comparison_schema_version": "Comparison contract version.",
        "generated_at": "UTC timestamp.",
        "traceforge_version": "TraceForge version.",
        "request": "The comparison request.",
        "baseline": "Baseline run (record run) or null when it could not be loaded.",
        "candidate": "Candidate run (record run) or null when it could not be loaded.",
        "matching": "Matching block with status 'refused' and its problems.",
        "non_finite_fields": "Optional.",
    },
    "per_scenario": {
        "scenario_id": "Structural scenario.",
        "family": "Task family.",
        "templates": "Templates mapped to this scenario.",
        "oracle_fingerprint": "Authoritative oracle fingerprint.",
        "source": "legacy_registry | catalogue.",
        "reason": "Why these templates form one scenario (legacy registry), else null.",
        "task_instances": "Task instances in this scenario.",
        "n_pairs": "Episode pairs.",
        "verdict_transitions": "'BASELINE->CANDIDATE' verdict counts.",
        "metrics": "verified_success {baseline_mean, candidate_mean, difference}.",
        "change": "improved | regressed | unchanged | mixed (descriptive).",
    },
    "per_task": {
        "task_id": "Task.",
        "task_version": "Task version.",
        "scenario_id": "Structural scenario.",
        "family": "Task family.",
        "template": "Template.",
        "split": "Split (registry).",
        "difficulty": "Difficulty (registry).",
        "n_pairs": "Episode pairs.",
        "seeds": "Episode seeds.",
        "verdict_transitions": "'BASELINE->CANDIDATE' verdict counts.",
        "metrics": "verified_success {baseline_mean, candidate_mean, difference}.",
        "change": "improved | regressed | unchanged | mixed (descriptive).",
    },
}
OPTIONAL_FIELDS = {
    "comparison_document": frozenset({"pairs", "non_finite_fields"}),
    "refusal_document": frozenset({"non_finite_fields"}),
}


def scenario_tier(n_scenarios: int) -> str:
    if n_scenarios < INSUFFICIENT_BELOW:
        return "insufficient_scenarios"
    if n_scenarios < EXPLORATORY_BELOW:
        return "exploratory_only"
    return "directional_eligible"


def record_problems(name: str, record: dict[str, object]) -> list[str]:
    schema = set(RECORD_SCHEMAS[name])
    optional = OPTIONAL_FIELDS.get(name, frozenset())
    fields = set(record)
    return [f"undocumented {name}.{f}" for f in sorted(fields - schema)] + [
        f"missing {name}.{f}" for f in sorted(schema - optional - fields)
    ]


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|")


def render_markdown() -> str:
    """docs/comparison-contract.md (generated; do not edit by hand)."""
    lines = [
        "# Comparison contract",
        "",
        f"Schema version **{COMPARISON_SCHEMA_VERSION}**. Generated from "
        "`src/traceforge/modules/evaluation/comparison_contract.py`; do not edit by hand "
        "(`tests/unit/test_comparison_contract.py` fails on drift).",
        "",
        "## Units and seeds",
        "",
        "- **Episode pair**: key `(task_id, task_version, episode_seed)`; one baseline and one "
        "candidate episode.",
        "- **Task instance**: `task_id@task_version`; secondary, descriptive cluster.",
        "- **Structural scenario**: templates with one authoritative oracle identity; the "
        "**primary resampling unit**. Seeds and instances never add clusters.",
        "- **Family**: reporting group only; never resampled.",
        "- Seeds: the benchmark **episode seed** is part of the pair key; the **provider "
        "generation seed** (`provider.seed`) must match for official comparisons; the "
        "**bootstrap seed** (`bootstrap_seed`) only drives resampling.",
        "",
        "## Estimands and intervals",
        "",
        "- Mean metrics: `sum of paired differences / usable pairs` (equals the difference of "
        "the arms' PR 1B pooled estimands; scenarios are weighted by their pairs).",
        "- Ratio metrics: `sum(num_C)/sum(den_C) - sum(num_B)/sum(den_B)`.",
        "- Primary interval: single-stage percentile bootstrap resampling structural scenarios "
        "(`cluster_bootstrap` on per-scenario paired differences; "
        "`paired_cluster_ratio_bootstrap` with one shared draw for both arms). A ratio replicate "
        "with a zero denominator makes the interval null, with the count reported.",
        "- Secondary `instance_sensitivity`: the same with task-instance clusters; "
        "anti-conservative when instances share a scenario; never interpreted.",
        "- No episode-level i.i.d. intervals, no family-cluster intervals, no p-values.",
        "- Unknown values exclude the pair from that metric only (`coverage`). Cost and token "
        "differences need complete coverage in both arms; lower bounds are never differenced.",
        "",
        "## Primary endpoint and evidence",
        "",
        f"- Primary endpoint: `{PRIMARY_GROUP}` / `{PRIMARY_METRIC}`. Without private_test pairs "
        "the endpoint is unavailable and pooled overall is **not** substituted.",
        f"- {ELIGIBILITY_NOTE}",
        "- `report_tier` (rigour), `evidence_tier` (what may be claimed) and `evidence_source` "
        "(model comparison or scripted pipeline validation) are separate fields. A final report "
        "may carry `exploratory_only`; it then states that no directional claim is supported.",
        "- A directional `improvement`/`regression` requires: evidence tier "
        "`directional_eligible`, an official strictly matched comparison, the primary endpoint "
        "and metric, and a primary interval excluding 0.",
        f"- public_test: {IN_DISTRIBUTION_NOTE}.",
        "",
        "Evidence tiers (first that applies): " + ", ".join(f"`{t}`" for t in EVIDENCE_TIERS) + ".",
        "",
        "Interpretation values (first that applies): "
        + ", ".join(f"`{t}`" for t in INTERPRETATIONS)
        + ".",
        "",
        "## Metrics",
        "",
        "| Key | Class | Kind | Direction | Population | Numerator | Denominator | "
        "Comparable when | PR 1B metric |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in COMPARABLE_METRICS:
        cells = (
            s.cls,
            s.kind,
            s.direction,
            s.population,
            s.numerator,
            s.denominator,
            s.comparable_when,
            s.pr1b_key or "none (comparison-only)",
        )
        lines.append(f"| `{s.key}` | " + " | ".join(_cell(c) for c in cells) + " |")
    lines += [
        "",
        "Class C (descriptive): latency "
        + ", ".join(f"`{k}`" for k in DESCRIPTIVE_LATENCY)
        + " p50/p90/p95 per arm with a point difference; no interval.",
        "",
        "## Matching problems",
        "",
        "| Code | Severity | Meaning |",
        "|---|---|---|",
        *[f"| `{c}` | {sev} | {_cell(m)} |" for c, (sev, m) in PROBLEM_CODES.items()],
        "",
        "Severity: `hard` refuses in every mode; `strict` refuses under strict coverage and is a "
        "warning under intersection; `not_official` keeps the comparison non-official.",
        "",
        f"## Legacy structural scenarios (registry version {SCENARIO_REGISTRY_VERSION})",
        "",
        "| Task version | Family | Template | Scenario | Oracle fingerprint | Reason |",
        "|---|---|---|---|---|---|",
        *[
            f"| {v} | {f} | {t} | `{e.scenario_id}` | `{e.oracle_fingerprint[:16]}…` | "
            f"{_cell(e.reason)} |"
            for (v, f, t), e in LEGACY_SCENARIOS.items()
        ],
        "",
        "## Limitations (stated in every report)",
        "",
        *[f"- {_cell(x)}" for x in LIMITATIONS],
        "",
        "## Record schemas",
        "",
    ]
    for name, fields in RECORD_SCHEMAS.items():
        lines += [f"### `{name}`", "", "| Field | Meaning |", "|---|---|"]
        lines += [f"| `{f}` | {_cell(d)} |" for f, d in fields.items()]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
