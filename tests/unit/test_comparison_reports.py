"""PR 2 comparison reports: required content in both views."""

from __future__ import annotations

from tests.unit.comparison_fakes import golden_compare
from traceforge.modules.evaluation.comparison_contract import LIMITATIONS
from traceforge.modules.evaluation.comparison_reports import (
    comparison_document,
    comparison_markdown,
)


def test_r1_markdown_shows_counts_tiers_seeds_methods_and_limitations() -> None:
    doc = comparison_document(golden_compare())
    md = comparison_markdown(doc)
    for text in (
        "4 structural scenarios (8 task instances, 16 episode pairs, 4 families)",
        "Report tier: **development**",
        "Evidence tier: **insufficient_scenarios**",
        "Evidence source: **model_comparison**",
        "## Primary endpoint",
        "`by_split.private_test` / `verified_success`",
        "Scenario-clustered CI",
        "Instance-clustered CI (sensitivity)",
        "Primary cluster unit: **structural_scenario**",
        "Episode seeds: [0, 1]",
        "bootstrap seed 0",
        "## Per structural scenario",
        "## Limitations",
        doc["comparison_fingerprint"],
    ):
        assert text in md, text
    for line in LIMITATIONS:
        assert line in md
    assert doc["report_type"] == "benchmark_comparison" and doc["comparison_schema_version"] == "1"
    assert doc["statistics"]["primary_cluster_unit"] == "structural_scenario"


def test_r1_same_inputs_reproduce_fingerprint_and_results_ids_stay_unique() -> None:
    from tests.unit.comparison_fakes import StaticResolver, golden_runs, golden_table, request
    from traceforge.contracts.verification import Verdict
    from traceforge.modules.evaluation.comparison import compare

    b, c = golden_runs()
    req = request()
    one = comparison_document(compare(b, c, req, StaticResolver(golden_table())))
    two = comparison_document(compare(b, c, req, StaticResolver(golden_table())))
    assert one["comparison_fingerprint"] == two["comparison_fingerprint"]
    assert one["groups"] == two["groups"] and one["per_task"] == two["per_task"]
    assert one["comparison_id"] != two["comparison_id"]
    c.records[0].verification.verdict = Verdict.FAIL
    three = compare(b, c, req, StaticResolver(golden_table()))
    assert three["comparison_fingerprint"] != one["comparison_fingerprint"]
