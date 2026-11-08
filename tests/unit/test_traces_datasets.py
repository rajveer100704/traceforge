from __future__ import annotations

import json

from tests.conftest import EXAMPLE_TRACES
from traceforge.contracts.datasets import CurationLevel, QualityStatus
from traceforge.contracts.traces import Outcome, SourceType
from traceforge.modules.trace_processing.dataset_builder import DatasetBuilder, read_partition
from traceforge.modules.trace_processing.ingestion import IngestionPipeline, split_jsonl
from traceforge.modules.trace_processing.normalization import normalize_record
from traceforge.modules.trace_processing.quality import QualityEngine
from traceforge.modules.training.sft.formatting import (
    assistant_mask,
    render_plain,
    trajectory_to_messages,
)


def _run():  # type: ignore[no-untyped-def]
    return IngestionPipeline(QualityEngine()).run("ex", split_jsonl(EXAMPLE_TRACES.read_bytes()))


def test_example_corpus_quality_report_is_computed() -> None:
    r = _run().report
    assert r.total_records == 13
    assert r.invalid == 4 and r.duplicates == 1 and r.rejected == 3 and r.accepted == 5
    assert r.by_reason["json_parse_error"] == 1 and r.by_reason["schema_invalid"] == 2
    assert r.by_reason["dangling_verification_ref"] == 1 and r.by_reason["orphan_tool_result"] == 1
    assert r.unverified_claimed_successes == 3
    assert r.parsed + r.invalid == r.total_records


def test_claimed_success_never_becomes_ground_truth() -> None:
    for t, _a, _raw in _run().trajectories:
        if t.verification_id is None:
            assert t.outcome == Outcome.UNKNOWN


def test_quarantine_has_actionable_details() -> None:
    for q, raw in _run().quarantined:
        assert q.reasons and raw.sha256 and q.line_number


def test_normalization_deterministic() -> None:
    line = EXAMPLE_TRACES.read_text().splitlines()[0]
    a, b = normalize_record(line, "ex", 1), normalize_record(line, "ex", 1)
    assert a.model_dump() == b.model_dump()


def test_chat_adapter() -> None:
    line = EXAMPLE_TRACES.read_text().splitlines()[4]
    t = normalize_record(line, "ex", 5)
    assert t.source_type == SourceType.IMPORTED and t.claimed_outcome == Outcome.SUCCESS
    assert [s.role.value for s in t.steps] == ["system", "user", "assistant", "tool", "assistant"]
    assert t.steps[3].tool_name == "read_file"


def test_sft_messages_mask_only_assistant() -> None:
    t = normalize_record(EXAMPLE_TRACES.read_text().splitlines()[0], "ex", 1)
    msgs = trajectory_to_messages(t)
    mask = assistant_mask(msgs)
    assert [m["role"] for m, k in zip(msgs, mask, strict=True) if k] == ["assistant"] * sum(mask)
    text, spans = render_plain(msgs)
    for a, b in spans:
        assert "<|tool|>" not in text[a:b] and "<|user|>" not in text[a:b]
    assert msgs[0]["content"].startswith("[verified outcome: UNKNOWN]")  # non-success tagged


def test_dataset_build_deterministic_and_lineage(run_policy, registry) -> None:  # type: ignore[no-untyped-def]
    trajectories = [
        run_policy(registry.bundle(t), p).trajectory
        for t in ("sqli-t0-s000", "authz-t0-s000", "sqli-t0-s004")
        for p in ("reference", "noop")
    ]
    known = {t.verification_id for t in trajectories}
    from traceforge.modules.trace_processing.validators import default_validators

    def pairs():  # type: ignore[no-untyped-def]
        q = QualityEngine(default_validators(None, known.__contains__))
        return [(t, q.assess(t)) for t in trajectories]

    split = {b.spec.task_id: b.oracle.split.value for b in registry.list()}
    a = DatasetBuilder().build(
        "x", pairs(), level=CurationLevel.D3_VERIFIED_SUCCESS, task_split=split.get
    )
    b = DatasetBuilder().build(
        "x", pairs(), level=CurationLevel.D3_VERIFIED_SUCCESS, task_split=split.get
    )
    assert a.manifest.content_hash == b.manifest.content_hash
    assert [p.sha256 for p in a.manifest.partitions] == [p.sha256 for p in b.manifest.partitions]
    rows = [r for name, data in a.partitions.items() for r in read_partition(data)]
    sources = dict(a.manifest.source_trajectories)
    assert rows and all(
        r["trajectory_id"] in sources and sources[r["trajectory_id"]] == r["content_hash"]
        for r in rows
    )
    assert {r["outcome"] for r in rows} == {"SUCCESS"}
    # sqli-t0-s004 is a public_test task: it must never enter a training dataset.
    assert all(r["task_id"] != "sqli-t0-s004" for r in rows)
    assert a.rejection_summary.get("split_leakage_excluded", 0) >= 1
    d4 = DatasetBuilder().build(
        "x", pairs(), level=CurationLevel.D4_VERIFIED_PLUS_INFORMATIVE, task_split=split.get
    )
    assert d4.manifest.content_hash != a.manifest.content_hash


def test_exact_duplicates_detected() -> None:
    line = EXAMPLE_TRACES.read_text().splitlines()[1]
    q = QualityEngine()
    first = q.assess(normalize_record(line, "a", 1))
    second = q.assess(normalize_record(line, "a", 2))
    assert first.status != QualityStatus.DUPLICATE and second.status == QualityStatus.DUPLICATE


def test_imported_without_provenance_rejected() -> None:
    lines = EXAMPLE_TRACES.read_text().splitlines()
    t = normalize_record(lines[5], "ex", 6)
    a = QualityEngine().assess(t)
    assert a.status == QualityStatus.REJECTED
    assert {"missing_provenance", "missing_license"} <= {i.code for i in a.issues}
    assert json.loads(lines[5])["id"] == "chat-002"


def test_informative_failure_requires_real_attempt(run_policy, registry) -> None:  # type: ignore[no-untyped-def]
    from traceforge.modules.trace_processing.quality import labels_for

    bundle = registry.bundle("sqli-t1-s000")
    overfit = run_policy(bundle, "visible_overfit").trajectory  # patched, refuted by hidden tests
    noop = run_policy(bundle, "noop").trajectory  # no patch at all
    tamper = run_policy(bundle, "test_tamper").trajectory  # policy violation
    assert "informative_failure" in labels_for(overfit, [])
    assert "informative_failure" not in labels_for(noop, [])
    assert "informative_failure" not in labels_for(tamper, [])
