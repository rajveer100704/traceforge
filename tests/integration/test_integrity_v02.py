"""Fail-closed benchmark integrity, lineage guards, repeated seeds and clustered statistics."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from traceforge.contracts.common import CapabilityUnavailable, ValidationFailure, sha256_json
from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig, TrainingLineage
from traceforge.contracts.tasks import Split
from traceforge.modules.evaluation.contamination import (
    check_prompt_rows,
    check_task_lineage,
    check_training_rows,
)
from traceforge.modules.evaluation.runner import BenchmarkIntegrityError, BenchmarkRunner


def _cfg(**kw: Any) -> BenchmarkConfig:
    base: dict[str, Any] = {
        "name": "integrity",
        "splits": (Split.PUBLIC_TEST,),
        "max_tasks_per_family": 1,
        "provider": ProviderConfig(policy="reference"),
        "bootstrap_samples": 200,
    }
    return BenchmarkConfig(**{**base, **kw})


def _ids(registry: Any, split: Split) -> list[str]:
    return [b.spec.task_id for b in registry.list(splits=[split])]


def test_config_hash_backward_compatible() -> None:
    cfg = BenchmarkConfig()
    legacy = {
        k: v
        for k, v in cfg.model_dump(mode="json").items()
        if k not in ("seeds", "run_mode", "training_lineage", "reward_thresholds")
    }
    # v0.3 provider pricing fields are hashed only when set (exact pre-v0.3 hash values are
    # pinned in tests/unit/test_provider_telemetry.py).
    legacy["provider"] = {
        k: v
        for k, v in legacy["provider"].items()
        if k not in ("price_per_1k_prompt", "price_per_1k_completion")
    }
    assert cfg.config_hash == sha256_json(legacy)
    assert BenchmarkConfig(seeds=(0, 1)).config_hash != cfg.config_hash
    with pytest.raises(ValueError):
        BenchmarkConfig(run_mode="lenient")


@pytest.mark.parametrize(
    "split", [Split.PRIVATE_TEST, Split.PUBLIC_TEST], ids=["private-template", "evaluated-task"]
)
def test_official_run_refuses_contaminated_lineage(platform: Any, split: Split) -> None:
    p = platform
    leaked = _ids(p.registry, split)[0]
    cfg = _cfg(training_lineage=TrainingLineage(task_ids=(leaked,)))
    with pytest.raises(BenchmarkIntegrityError) as err:
        asyncio.run(p.evaluations.run(cfg))
    report = err.value.report
    assert not report.ok and report.training_overlap
    assert any(leaked in prob for prob in report.training_overlap)
    # refused before any rollout, and the full leakage report is persisted
    from traceforge.infrastructure.database import models as m

    with p.db.session() as s:
        assert s.query(m.TaskRun).count() == 0
        ev = s.query(m.EvaluationRun).one()
        assert ev.status == "refused" and ev.metrics["leakage"]["training_overlap"]
    refusal = json.loads((p.settings.reports_dir / str(ev.id) / "refusal.json").read_text())
    assert refusal["status"] == "refused" and "training_overlap" in refusal["leakage"]["checks"]


def test_diagnostic_mode_runs_but_is_marked_non_official(platform: Any) -> None:
    p = platform
    leaked = _ids(p.registry, Split.PRIVATE_TEST)[0]
    cfg = _cfg(run_mode="diagnostic", training_lineage=TrainingLineage(task_ids=(leaked,)))
    result, paths = asyncio.run(p.evaluations.run(cfg))
    doc = json.loads(open(paths["report_json"], encoding="utf-8").read())
    assert doc["official"] is False and doc["leakage"]["ok"] is False
    assert "NON-OFFICIAL" in open(paths["report_md"], encoding="utf-8").read()
    assert result.runs  # executed


def test_clean_training_lineage_passes_and_is_recorded(platform: Any) -> None:
    p = platform
    train = _ids(p.registry, Split.TRAIN)[:3]
    cfg = _cfg(training_lineage=TrainingLineage(task_ids=(*train, "imported-trace-xyz")))
    _result, paths = asyncio.run(p.evaluations.run(cfg))
    leak = json.loads(open(paths["report_json"], encoding="utf-8").read())["leakage"]
    assert leak["ok"] and leak["training_task_ids"] == sorted([*train, "imported-trace-xyz"])
    assert leak["unlinked_training_task_ids"] == ["imported-trace-xyz"]
    assert set(leak["checks"]) >= {"task_lineage", "training_overlap", "split_fixture_disjoint"}


def test_unknown_training_run_lineage_is_refused(platform: Any) -> None:
    cfg = _cfg(
        training_lineage=TrainingLineage(training_run_ids=("00000000-0000-0000-0000-000000000000",))
    )
    with pytest.raises(BenchmarkIntegrityError, match="lineage"):
        asyncio.run(platform.evaluations.run(cfg))


def test_dataset_lineage_is_resolved_from_partitions(platform: Any) -> None:
    from traceforge.application.demo import EXAMPLE_TRACES
    from traceforge.contracts.datasets import CurationLevel

    p = platform
    p.ingestion.ingest_bytes(EXAMPLE_TRACES.name, EXAMPLE_TRACES.read_bytes())
    built = p.datasets.build("lineage-check", level=CurationLevel.D1_RAW)
    cfg = _cfg(training_lineage=TrainingLineage(dataset_version_ids=(built["dataset_version_id"],)))
    _result, paths = asyncio.run(p.evaluations.run(cfg))
    leak = json.loads(open(paths["report_json"], encoding="utf-8").read())["leakage"]
    assert (
        leak["ok"] and f"dataset_version:{built['dataset_version_id']}" in leak["training_sources"]
    )


def test_tampered_task_lineage_blocks_official_runs(registry: Any, executor: Any) -> None:
    from traceforge.modules.tasks.registry import TaskRegistry

    good = registry.list(splits=[Split.PUBLIC_TEST])[0]
    tampered = good.model_copy(
        update={"hidden_files": {k: v + "\n# edited" for k, v in good.hidden_files.items()}}
    )
    assert any("fixture hash" in p for p in check_task_lineage([tampered]))
    assert check_task_lineage(registry.list()) == []
    reg = TaskRegistry()
    for b in registry.list():
        reg.register(tampered if b.spec.task_id == good.spec.task_id else b)
    runner = BenchmarkRunner(executor, reg)
    report = runner.validate(_cfg())
    assert not report.ok
    with pytest.raises(BenchmarkIntegrityError):
        runner.enforce(_cfg(), report)
    runner.enforce(_cfg(run_mode="diagnostic"), report)  # explicit diagnostic mode only


def test_training_rows_and_grpo_prompts_reject_heldout_tasks(registry: Any) -> None:
    from traceforge.modules.training.grpo.adapter import (
        assert_training_prompts_only,
        rl_prompt_rows,
    )

    held = _ids(registry, Split.PRIVATE_TEST)[0]
    train = _ids(registry, Split.TRAIN)[0]
    assert (
        check_training_rows([{"task_id": train, "split": "train"}, {"task_id": ""}], registry) == []
    )
    assert check_training_rows([{"task_id": held, "split": "train"}], registry)
    assert check_training_rows([{"task_id": "imported-unknown", "split": "train"}], registry) == []
    rows = rl_prompt_rows(registry)
    assert rows and check_prompt_rows(rows, registry) == []
    assert rl_prompt_rows(registry, splits=("public_test", "private_test")) == []
    with pytest.raises(CapabilityUnavailable, match="leakage guard"):
        assert_training_prompts_only([*rows, {"task_id": held}], registry)
    with pytest.raises(CapabilityUnavailable):
        assert_training_prompts_only([{"task_id": "nope"}], registry)


def test_training_service_refuses_contaminated_dataset(platform: Any, tmp_path: Any) -> None:
    pytest.importorskip("torch")
    import pyarrow as pa
    import pyarrow.parquet as pq

    from traceforge.application.training import TrainingService
    from traceforge.modules.trace_processing.dataset_builder import SFT_SCHEMA

    held = _ids(platform.registry, Split.PRIVATE_TEST)[0]
    row = {
        "trajectory_id": "t",
        "content_hash": "h",
        "task_id": held,
        "split": "train",
        "outcome": "SUCCESS",
        "quality_score": 1.0,
        "labels": [],
        "assistant_targets": 1,
        "messages_json": json.dumps([{"role": "assistant", "content": "x"}]),
        "format_version": "1",
    }
    for name in ("train", "validation"):
        pq.write_table(pa.Table.from_pylist([row], schema=SFT_SCHEMA), tmp_path / f"{name}.parquet")
    with pytest.raises(ValidationFailure, match="leakage guard"):
        TrainingService(platform).run(
            {"mode": "cpu-test", "config": {"dataset_dir": str(tmp_path)}}
        )
    from traceforge.infrastructure.database import models as m

    with platform.db.session() as s:
        run = s.query(m.TrainingRun).one()
        assert run.status == "blocked" and "leakage guard" in run.blocked_reason


def test_repeated_seeds_and_task_clustered_metrics(platform: Any) -> None:
    cfg = _cfg(seeds=(0, 1, 2))
    result, paths = asyncio.run(platform.evaluations.run(cfg))
    o = result.metrics["overall"]
    n_tasks = len({r.task_id for r in result.runs})
    assert o["n_episodes"] == 3 * n_tasks and o["n_tasks"] == n_tasks and o["n_seeds"] == 3
    assert set(result.metrics["by_seed"]) == {"0", "1", "2"}
    assert {r.metadata["seed"] for r in result.runs} == {0, 1, 2}
    tc = o["task_clustered"]["verified_success"]
    assert tc["n_clusters"] == n_tasks and tc["n_observations"] == 3 * n_tasks
    rows = result.metrics["task_level"]
    assert len(rows) == n_tasks and all(r["n_episodes"] == 3 for r in rows)
    assert all(r["outcome_consistent_across_episodes"] for r in rows)  # deterministic scripts
    ev = result.metrics["evidence"]
    assert ev["learned_model_evidence"] is False and "deterministic" in ev["repeated_seed_note"]
    sens = result.metrics["reward_threshold_sensitivity"]
    rates = [s["reward_positive_rate"]["rate"] for s in sens]
    assert rates == sorted(rates, reverse=True)  # non-increasing in the threshold
    md = open(paths["report_md"], encoding="utf-8").read()
    assert "Task-clustered uncertainty" in md and "Reward-threshold sensitivity" in md
    assert "SCRIPTED-AGENT" in json.dumps(result.metrics["evidence"])
