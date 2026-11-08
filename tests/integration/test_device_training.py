"""Device handling inside the real SFT loop (requires the optional ml extra)."""

from __future__ import annotations

from typing import Any

import pytest

from traceforge.contracts.common import CapabilityUnavailable

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")


def _rows() -> list[dict[str, Any]]:
    import json

    msgs = [
        {"role": "system", "content": "fix it"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "I will read the file and patch it."},
    ]
    return [{"messages_json": json.dumps(msgs), "task_id": "", "split": "train"}] * 3


def test_cpu_test_sft_runs_on_cpu_and_moves_every_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from traceforge.modules.training.sft import trainer

    seen: list[str] = []
    original = trainer.to_device

    def spy(batch: Any, device: Any) -> Any:
        moved = original(batch, device)
        seen.extend(str(t.device) for t in moved)
        return moved

    monkeypatch.setattr(trainer, "to_device", spy)
    cfg = trainer.SFTConfig(mode="cpu-test", max_steps=2, output_dir=str(tmp_path), max_length=512)
    meta = trainer.run_sft(cfg, _rows(), _rows()[:1])
    assert meta["device"]["device"] == "cpu" and meta["device"]["precision"] == "fp32"
    assert meta["environment"]["python"] and meta["step"] == 2
    assert seen and set(seen) == {"cpu"}  # input_ids, labels, attention_mask (train + val)


def test_cpu_test_with_cuda_request_fails_closed_without_gpu(tmp_path: Any) -> None:
    from traceforge.modules.training.sft import trainer

    if torch.cuda.is_available():
        pytest.skip("CUDA present: fail-closed path not reachable")
    cfg = trainer.SFTConfig(mode="cpu-test", device="cuda", output_dir=str(tmp_path))
    with pytest.raises(CapabilityUnavailable, match="device=cuda"):
        trainer.run_sft(cfg, _rows(), [])


@pytest.mark.skipif(not torch.cuda.is_available(), reason="no CUDA device on this machine")
def test_real_cuda_sft_step(tmp_path: Any) -> None:  # pragma: no cover - needs a GPU
    from traceforge.modules.training.sft import trainer

    cfg = trainer.SFTConfig(mode="cpu-test", device="cuda", max_steps=1, output_dir=str(tmp_path))
    meta = trainer.run_sft(cfg, _rows(), [])
    assert meta["device"]["device"] == "cuda:0" and meta["device"]["gpu_count"] >= 1
