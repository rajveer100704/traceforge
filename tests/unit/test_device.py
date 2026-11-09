"""Device/precision selection: CPU behaviour, mocked CUDA, and (when present) real CUDA."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from traceforge.contracts.common import CapabilityUnavailable, sha256_json
from traceforge.modules.training.device import (
    grpo_device_arguments,
    resolve_device,
)


def fake_torch(*, cuda: bool, bf16: bool = True, names: tuple[str, ...] = ("Fake GPU",)) -> Any:
    props = SimpleNamespace(total_memory=24 * 10**9)
    return SimpleNamespace(
        __version__="9.9.9+fake",
        version=SimpleNamespace(cuda="12.8" if cuda else None),
        cuda=SimpleNamespace(
            is_available=lambda: cuda,
            device_count=lambda: len(names) if cuda else 0,
            get_device_name=lambda i: names[i],
            get_device_properties=lambda i: props,
            is_bf16_supported=lambda: bf16,
        ),
        backends=SimpleNamespace(cudnn=SimpleNamespace(version=lambda: 90100)),
    )


def test_auto_without_cuda_is_cpu_fp32() -> None:
    info = resolve_device("auto", "auto", torch_module=fake_torch(cuda=False))
    assert (info.device, info.device_type, info.precision) == ("cpu", "cpu", "fp32")
    assert info.cuda_available is False and info.gpu_count == 0 and info.gpu_names == ()
    assert info.as_dict()["requested_device"] == "auto"


def test_cuda_requested_without_cuda_fails_closed() -> None:
    with pytest.raises(CapabilityUnavailable, match="refusing to fall back"):
        resolve_device("cuda", "auto", torch_module=fake_torch(cuda=False))


def test_fp16_on_cpu_rejected_bf16_on_cpu_allowed() -> None:
    with pytest.raises(CapabilityUnavailable, match="fp16 requires a CUDA"):
        resolve_device("cpu", "fp16", torch_module=fake_torch(cuda=False))
    assert resolve_device("cpu", "bf16", torch_module=fake_torch(cuda=False)).precision == "bf16"


def test_explicit_cpu_with_cuda_present_stays_on_cpu() -> None:
    info = resolve_device("cpu", "auto", torch_module=fake_torch(cuda=True))
    assert info.device == "cpu" and info.precision == "fp32" and info.cuda_available


def test_mocked_cuda_auto_selects_gpu_and_records_hardware() -> None:
    info = resolve_device("auto", "auto", torch_module=fake_torch(cuda=True, names=("A", "B")))
    assert info.device == "cuda:0" and info.is_cuda and info.precision == "bf16"
    assert info.gpu_names == ("A", "B") and info.gpu_memory_gb == (24.0, 24.0)
    assert info.cuda_version == "12.8" and info.cudnn_version == 90100
    assert info.autocast_dtype_name == "bfloat16"


def test_mocked_cuda_without_bf16() -> None:
    torch = fake_torch(cuda=True, bf16=False)
    assert resolve_device("cuda", "auto", torch_module=torch).precision == "fp16"
    with pytest.raises(CapabilityUnavailable, match="bfloat16"):
        resolve_device("cuda", "bf16", torch_module=torch)


def test_invalid_settings_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_device("tpu", "auto", torch_module=fake_torch(cuda=False))
    with pytest.raises(ValueError):
        resolve_device("cpu", "int8", torch_module=fake_torch(cuda=False))


def test_grpo_device_flags_follow_resolved_device() -> None:
    cpu = resolve_device("auto", "auto", torch_module=fake_torch(cuda=False))
    gpu = resolve_device("auto", "auto", torch_module=fake_torch(cuda=True))
    assert grpo_device_arguments(cpu) == {"use_cpu": True, "bf16": False, "fp16": False}
    assert grpo_device_arguments(gpu) == {"use_cpu": False, "bf16": True, "fp16": False}


def test_grpo_arguments_no_unconditional_cpu() -> None:
    from traceforge.modules.training.grpo.trainer import GRPOTrainingConfig, grpo_arguments

    cfg = GRPOTrainingConfig(base_model="local-model", device="cuda", precision="fp16")
    gpu = resolve_device("cuda", "fp16", torch_module=fake_torch(cuda=True))
    args = grpo_arguments(cfg, gpu)
    assert args["use_cpu"] is False and args["fp16"] is True and args["bf16"] is False
    cpu = resolve_device("cpu", "auto", torch_module=fake_torch(cuda=False))
    assert grpo_arguments(cfg, cpu)["use_cpu"] is True


def test_config_hashes_stable_for_v01_configs() -> None:
    from traceforge.modules.training.grpo.trainer import GRPOTrainingConfig
    from traceforge.modules.training.sft.trainer import SFTConfig

    sft = SFTConfig()
    legacy = {
        k: v for k, v in sft.model_dump(mode="json").items() if k not in ("device", "precision")
    }
    assert sft.config_hash == sha256_json(legacy)
    assert SFTConfig(device="cuda").config_hash != sft.config_hash
    grpo = GRPOTrainingConfig(base_model="m")
    legacy = {
        k: v for k, v in grpo.model_dump(mode="json").items() if k not in ("device", "precision")
    }
    assert grpo.config_hash == sha256_json(legacy)


def test_cpu_test_defaults_to_cpu_sft_defaults_to_auto() -> None:
    from traceforge.modules.training.sft.trainer import SFTConfig

    assert SFTConfig(mode="cpu-test").effective_device == "cpu"
    assert SFTConfig(mode="sft").effective_device == "auto"
    assert SFTConfig(mode="cpu-test", device="auto").effective_device == "auto"
    with pytest.raises(ValueError):
        SFTConfig(device="gpu")
