"""Optional integrations: cpu-test SFT (real optimizer steps on a tiny local model) and Docker."""

from __future__ import annotations

import importlib.util
import json
import math

import pytest

from traceforge.contracts.common import CapabilityUnavailable

HAS_ML = (
    importlib.util.find_spec("torch") is not None
    and importlib.util.find_spec("transformers") is not None
)


def _rows(run_policy, registry):  # type: ignore[no-untyped-def]
    rows = []
    from traceforge.modules.training.sft.formatting import trajectory_to_messages

    for tid in ("sqli-t0-s000", "authz-t0-s000", "ssrf-t0-s000"):
        t = run_policy(registry.bundle(tid), "reference").trajectory
        rows.append({"messages_json": json.dumps(trajectory_to_messages(t), default=str)})
    return rows


def test_label_masking_char_tokenizer(run_policy, registry) -> None:  # type: ignore[no-untyped-def]
    from traceforge.modules.training.sft.trainer import IGNORE_INDEX, CharTokenizer, encode_example

    msgs = json.loads(_rows(run_policy, registry)[0]["messages_json"])
    ids, labels = encode_example(msgs, CharTokenizer(), 100_000)
    assert len(ids) == len(labels)
    targets = bytes(t - 2 for t in labels if t != IGNORE_INDEX).decode("utf-8", "replace")
    assert "read_file" in targets  # assistant tool calls are targets
    assert "sandbox policy" not in targets and '"run_id"' not in targets  # tool observations masked
    assert any(lbl == IGNORE_INDEX for lbl in labels)


@pytest.mark.ml
@pytest.mark.skipif(not HAS_ML, reason="ml extra not installed")
def test_cpu_test_sft_trains_and_resumes(run_policy, registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from traceforge.modules.training.sft.trainer import SFTConfig, run_sft

    rows = _rows(run_policy, registry)
    cfg = SFTConfig(
        mode="cpu-test",
        output_dir=str(tmp_path / "a"),
        max_steps=6,
        epochs=3,
        batch_size=1,
        max_length=8192,
    )
    meta = run_sft(cfg, rows[:2], rows[2:])
    assert meta["step"] == 6 and all(math.isfinite(x) for x in meta["loss_curve"])
    assert (
        meta["last_loss"] < meta["first_loss"]
    )  # real gradient steps reduce loss on this tiny set
    resumed = run_sft(
        cfg.model_copy(
            update={
                "resume_from": str(tmp_path / "a" / "step-6"),
                "max_steps": 8,
                "output_dir": str(tmp_path / "b"),
            }
        ),
        rows[:2],
        rows[2:],
    )
    assert resumed["step"] == 8
    with pytest.raises(CapabilityUnavailable, match="no trainable examples"):
        run_sft(cfg.model_copy(update={"max_length": 64}), rows[:2], [])


def test_sft_without_dataset_is_blocked() -> None:
    if not HAS_ML:
        pytest.skip("ml extra not installed")
    from traceforge.modules.training.sft.trainer import SFTConfig, run_sft

    with pytest.raises(CapabilityUnavailable):
        run_sft(SFTConfig(), [], [])


def test_sft_mode_without_local_model_is_blocked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    if not HAS_ML:
        pytest.skip("ml extra not installed")
    from traceforge.modules.training.sft.trainer import SFTConfig, run_sft

    cfg = SFTConfig(
        mode="sft", base_model="org/definitely-not-downloaded-model", output_dir=str(tmp_path)
    )
    with pytest.raises(CapabilityUnavailable, match="not available locally"):
        run_sft(cfg, [{"messages_json": '[{"role": "assistant", "content": "x"}]'}], [])


def _docker_ready() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get("traceforge-sandbox:0.1")
        return True
    except Exception:
        return False


@pytest.mark.docker
@pytest.mark.skipif(
    not _docker_ready(), reason="Docker daemon or traceforge-sandbox:0.1 image unavailable"
)
def test_docker_provider_runs_and_isolates(registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from traceforge.infrastructure.sandbox.docker_provider import DockerSandboxProvider

    provider = DockerSandboxProvider("traceforge-sandbox:0.1", tmp_path)
    bundle = registry.bundle("sqli-t0-s000")
    handle = provider.create("r", bundle.workspace_files, bundle.spec.budget)
    try:
        result = provider.run_tests(handle, "tests/test_visible.py", "", 60)
        assert result.harness_ok and result.total == 3
        probe = "import os\ndef test_net():\n    import urllib.parse\n    assert os.getuid() != 0\n"
        r = provider.run_isolated_verifier(
            {"app/__init__.py": ""}, {"t.py": probe}, "t.py", bundle.spec.budget
        )
        assert r.result_parsed
    finally:
        provider.destroy(handle)
    provider.destroy(handle)  # idempotent
