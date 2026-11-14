"""The metric contract registry is complete, accurate and in sync with its documentation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tests.unit.metric_fakes import ep
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.metric_contract import (
    METRICS,
    METRICS_SCHEMA_VERSION,
    emitted_keys,
    render_markdown,
)
from traceforge.modules.evaluation.metrics import summarize

DOC = Path(__file__).resolve().parents[2] / "docs" / "metrics-contract.md"


def _outputs() -> list[dict[str, Any]]:
    runs = [
        ep(),
        ep("t2", Verdict.FAIL, reward=0.0, cost=None),
        ep("t3", termination="provider_error", provider_failure="other"),
    ]
    labels = {"t1": True, "t2": False, "t3": True}
    return [
        summarize([], bootstrap_samples=100),  # type: ignore[arg-type]
        summarize(runs, bootstrap_samples=100),  # type: ignore[arg-type]
        summarize(runs, bootstrap_samples=100, ground_truth=lambda r: labels[r.task_id]),  # type: ignore[arg-type]
    ]


def test_every_emitted_key_is_registered_and_every_registered_key_is_emitted() -> None:
    emitted: set[str] = set()
    for out in _outputs():
        keys = emitted_keys(out)
        assert keys - set(METRICS) == set(), f"unregistered: {sorted(keys - set(METRICS))}"
        emitted |= keys
    assert set(METRICS) - emitted == set(), (
        f"registered but never emitted: {sorted(set(METRICS) - emitted)}"
    )


def test_output_declares_schema_version() -> None:
    assert all(o["metrics_schema_version"] == METRICS_SCHEMA_VERSION == "2" for o in _outputs())


def test_proportions_declare_a_real_denominator_and_wilson_or_cluster_interval() -> None:
    for spec in METRICS.values():
        if spec.kind == "proportion":
            assert spec.denominator not in ("", "n/a"), spec.key
            assert spec.interval != "none", spec.key
        for field in (
            "numerator",
            "denominator",
            "unit",
            "statistical_unit",
            "interval",
            "missing",
        ):
            assert getattr(spec, field), (spec.key, field)


def test_docs_are_generated_from_the_registry() -> None:
    assert DOC.read_text(encoding="utf-8") == render_markdown(), (
        "docs/metrics-contract.md is out of date: regenerate it with "
        "metric_contract.render_markdown()"
    )


def test_docs_table_rows_have_exactly_nine_columns() -> None:
    rows = [ln for ln in render_markdown().splitlines() if ln.startswith("| `")]
    assert len(rows) == len(METRICS)
    for row in rows:
        cells = re.split(r"(?<!\\)\|", row.strip())[1:-1]  # unescaped pipes delimit cells
        assert len(cells) == 9, row
