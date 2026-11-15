"""PR 2 comparison contract: generated docs, documented fields, Class A metric provenance."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from tests.unit.comparison_fakes import golden_compare
from traceforge.modules.evaluation.comparison_contract import (
    COMPARABLE_METRICS,
    PROBLEM_CODES,
    RECORD_SCHEMAS,
    record_problems,
    render_markdown,
)
from traceforge.modules.evaluation.comparison_reports import (
    comparison_document,
    comparison_markdown,
)
from traceforge.modules.evaluation.metric_contract import METRICS, lookup

DOC = Path(__file__).resolve().parents[2] / "docs" / "comparison-contract.md"


def test_docs_are_generated_from_the_contract() -> None:
    assert DOC.read_text(encoding="utf-8") == render_markdown(), (
        "docs/comparison-contract.md is out of date: regenerate with "
        "comparison_contract.render_markdown()"
    )


def test_class_a_metrics_are_registered_pr1b_metrics() -> None:
    for s in COMPARABLE_METRICS:
        if s.cls == "A":
            assert s.pr1b_key is not None and lookup(s.pr1b_key) is not None, s.key
            assert s.pr1b_key in METRICS
        else:
            assert s.pr1b_key is None


def _audit(doc: dict[str, Any]) -> list[str]:
    problems = record_problems("comparison_document", doc)
    problems += record_problems("seeds", doc["seeds"])
    problems += record_problems("run", doc["baseline"]) + record_problems("run", doc["candidate"])
    problems += record_problems("matching", doc["matching"])
    problems += record_problems("protocol", doc["protocol"])
    problems += record_problems("primary_endpoint", doc["primary_endpoint"])
    groups = [doc["groups"]["overall"]]
    for kind in ("by_split", "by_family", "by_difficulty"):
        groups += list(doc["groups"][kind].values())
    for g in groups:
        problems += record_problems("group", g)
        for m in g["metrics"].values():
            problems += record_problems("metric_comparison", m)
            problems += record_problems("difference", m["difference"])
            problems += record_problems("sensitivity", m["instance_sensitivity"])
            problems += record_problems("arm_estimate", m["baseline"])
            problems += record_problems("arm_estimate", m["candidate"])
    for row in doc["per_scenario"]:
        problems += record_problems("per_scenario", row)
    for row in doc["per_task"]:
        problems += record_problems("per_task", row)
    for entry in doc["latency"].values():
        problems += record_problems("latency_entry", entry)
    for name in ("cost_coverage", "usage_coverage"):
        problems += record_problems("coverage_block", doc[name])
        problems += record_problems("coverage_arm", doc[name]["baseline"])
        problems += record_problems("coverage_arm", doc[name]["candidate"])
    problems += record_problems("statistics", doc["statistics"])
    problems += record_problems("report", doc["report"])
    for row in doc.get("pairs", []):
        problems += record_problems("pair", row)
    for p in doc["matching"]["problems"]:
        assert p["code"] in PROBLEM_CODES
    return problems


def test_every_emitted_field_is_documented() -> None:
    for kw in ({}, {"include_pairs": True}):
        doc = comparison_document(golden_compare(**kw))
        assert _audit(doc) == []
    assert set(RECORD_SCHEMAS) >= {"comparison_document", "group", "metric_comparison"}


def test_documents_are_finite_and_markdown_uses_the_same_values() -> None:
    core = golden_compare()
    core["groups"]["overall"]["metrics"]["mean_reward"]["difference"]["estimate"] = math.nan
    core["latency"]["episode_seconds"]["difference"]["p50"] = math.inf
    doc = comparison_document(core)
    assert doc["non_finite_fields"] == [
        "groups.overall.metrics.mean_reward.difference.estimate",
        "latency.episode_seconds.difference.p50",
    ]
    assert doc["groups"]["overall"]["metrics"]["mean_reward"]["difference"]["estimate"] is None
    md = comparison_markdown(doc)
    assert not re.search(r"\b(nan|-?inf|infinity)\b", md, re.IGNORECASE)


def test_refusal_documents_match_their_schema() -> None:
    import pytest

    from tests.unit.comparison_fakes import StaticResolver, golden_runs, golden_table, request
    from traceforge.modules.evaluation.comparison import ComparisonRefused, compare
    from traceforge.modules.evaluation.comparison_reports import refusal_document

    b, c = golden_runs()
    c.records = c.records[:-1]
    with pytest.raises(ComparisonRefused) as exc:
        compare(b, c, request(), StaticResolver(golden_table()))
    doc = refusal_document(exc.value.document)
    assert record_problems("refusal_document", doc) == []
    assert record_problems("run", doc["baseline"]) == []
    assert record_problems("matching", doc["matching"]) == []
