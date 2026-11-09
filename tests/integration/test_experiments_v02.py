"""Attack catalogue, held-out attacks, reproducibility bundles, Experiments F and G."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from traceforge.modules.agents.providers.demo import ADVERSARIAL_POLICIES, POLICIES, all_policies
from traceforge.modules.agents.providers.heldout_attacks import (
    HELDOUT_POLICIES,
    exception_masking_source,
    input_denylist_source,
    public_functions,
    universal_equality_source,
)
from traceforge.modules.evaluation import attack_catalog


def test_attack_sets_are_disjoint_and_versioned() -> None:
    dev, held = attack_catalog.by_set("development"), attack_catalog.by_set("heldout")
    assert set(dev) == set(ADVERSARIAL_POLICIES)
    assert set(held) == set(HELDOUT_POLICIES)
    assert not set(dev) & set(held)
    assert not set(held) & set(POLICIES)  # Experiment B (development) never runs held-out attacks
    assert set(all_policies()) == set(POLICIES) | set(HELDOUT_POLICIES)
    assert set(attack_catalog.known_at("1.0.0")) == set(dev)
    assert all(not HELDOUT_POLICIES[a][0].deserves_reward for a in held)
    doc = attack_catalog.catalog_document()
    assert doc["catalog_version"] and len(doc["attacks"]) == len(dev) + len(held)


def test_heldout_rewriters_keep_module_parseable(registry: Any) -> None:
    import ast

    for b in registry.list()[:: max(1, len(registry.list()) // 6)]:
        module = str(b.spec.public_metadata["module"])
        src = b.workspace_files[module]
        assert public_functions(src)
        for transform in (
            universal_equality_source,
            exception_masking_source,
            input_denylist_source,
        ):
            ast.parse(transform(src))


@pytest.mark.parametrize("policy", ["report_injection", "stale_evidence"])
def test_text_and_evidence_attacks_get_no_reward(
    registry: Any, run_policy: Any, policy: str
) -> None:
    art = run_policy(registry.bundle("sqli-t0-s000"), policy)
    assert art.summary.reward.final_reward == 0.0
    assert art.summary.verification.verdict.value != "PASS"


def test_reproducibility_bundle_roundtrip(platform: Any, tmp_path: Path) -> None:
    from traceforge.application.reproducibility import verify_bundle, write_bundle

    doc = {"metric": 1.0}
    out = write_bundle(
        platform,
        tmp_path,
        experiment="X",
        document=doc,
        configs={"cfg": {"a": 1}},
        seeds=[0, 1],
        episodes=[{"seed": 0, "summary": {"rollout_id": "r"}}],
    )
    bundle = Path(out["dir"])
    meta = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    assert verify_bundle(bundle) == []
    prov = meta["provenance"]
    assert prov["verifier"]["config_hash"] and prov["reward_policies"]["verified-v1"]["policy_hash"]
    assert prov["task_catalogue_hash"] and prov["n_tasks"] == 39
    assert meta["seeds"] == [0, 1] and meta["configs"]["cfg"]["config_hash"]
    assert meta["episodes"]["count"] == 1 and "episodes.jsonl.gz" in meta["files_sha256"]
    assert meta["environment"]["python"] and "device" in meta["environment"]
    assert "git" in meta and (bundle / "REPRODUCE.md").is_file()
    (bundle / "metrics.json").write_text("{}", encoding="utf-8")
    assert verify_bundle(bundle) == ["hash mismatch metrics.json"]


def test_experiment_g_runs_configured_arms_only(platform: Any, tmp_path: Path) -> None:
    from traceforge.application.experiments import run_experiment

    cfg = tmp_path / "g.yaml"
    cfg.write_text(
        "benchmark: {splits: [public_test], seeds: [0, 1]}\n"
        "arms:\n"
        "  pipeline_check: {provider: {kind: demo, policy: reference}}\n"
        "  sft: {provider: null, training_run_ids: []}\n",
        encoding="utf-8",
    )
    out = run_experiment(platform, "G", tmp_path / "out", config_path=cfg)
    assert out["headline"] == {"pipeline_check": "EXECUTED", "sft": "NOT EXECUTED"}
    doc = json.loads(Path(out["files"]["json"]).read_text(encoding="utf-8"))
    arm = doc["arms"]["pipeline_check"]
    assert arm["learned_model"] is False and doc["evidence"]["learned_model_evidence"] is False
    m = arm["metrics"]
    assert m["n_episodes"] == 2 * m["n_tasks"]
    for key in (
        "hidden_security_pass_rate",
        "regression_failure_rate",
        "false_positive_reward_rate",
        "tool_calls_per_verified_success",
        "verified_success_task_clustered",
        "by_family",
        "by_difficulty",
    ):
        assert key in m
    assert doc["comparison"].startswith("NOT AVAILABLE")
    meta = json.loads((Path(out["bundle"]) / "bundle.json").read_text(encoding="utf-8"))
    assert meta["episodes"]["count"] == m["n_episodes"] and meta["evaluation_ids"]


def test_experiment_f_controls_and_honest_status(platform: Any, tmp_path: Path) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from traceforge.application.experiments import run_experiment

    cfg = tmp_path / "f.yaml"
    cfg.write_text(
        "training: {mode: cpu-test, max_length: 2048, max_steps: 1, batch_size: 1}\n"
        "seeds: [0]\nevaluation: {splits: [private_test], providers: {}}\n",
        encoding="utf-8",
    )
    out = run_experiment(platform, "F", tmp_path / "out", config_path=cfg)
    doc = json.loads(Path(out["files"]["json"]).read_text(encoding="utf-8"))
    assert set(doc["arms"]) == {
        "d1_raw",
        "d2_schema_valid",
        "d3_verified_success",
        "d4_verified_plus_informative",
    }
    hashes = {a["control_hash"] for a in doc["arms"].values()}
    assert len(hashes) == 1  # identical controls across arms
    for arm in doc["arms"].values():
        assert arm["evaluation"]["status"] == "NOT EXECUTED"
        assert all(t["status"] in ("EXECUTED", "NOT EXECUTED", "REFUSED") for t in arm["training"])
    assert doc["model_improvement_claim"].startswith("NONE")
    assert doc["evidence"]["learned_model_evidence"] is False
    meta = json.loads((Path(out["bundle"]) / "bundle.json").read_text(encoding="utf-8"))
    assert len(meta["datasets"]) == 4 and meta["models"][0]["pretrained"] is False
